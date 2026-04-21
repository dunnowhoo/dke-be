# -*- coding: utf-8 -*-

import base64
import json
import logging
import re
import threading

import requests as _requests

from odoo import http, Command, registry as odoo_registry, api as odoo_api
from odoo.http import request
from odoo.addons.phone_validation.tools import phone_validation

_logger = logging.getLogger(__name__)


def _marketing_broadcast_worker(dbname, uid, campaign_id, partner_ids, template_id, wa_account_id, test_mode):
    """Background thread: sends WA messages for a marketing campaign.

    Runs in its own DB cursor so the HTTP request can return immediately.
    Updates campaign.state to 'done' (or 'cancelled' on fatal error) when finished.
    """
    try:
        with odoo_registry(dbname).cursor() as cr:
            env = odoo_api.Environment(cr, uid, {})
            template = env['whatsapp.template'].sudo().browse(template_id)
            campaign = env['dke.marketing.campaign'].sudo().browse(campaign_id)
            sent = 0
            failed = 0

            if test_mode:
                author_id = env.user.partner_id.id
                for pid in partner_ids:
                    try:
                        env['mail.message'].sudo().create({
                            'model': 'res.partner',
                            'res_id': pid,
                            'message_type': 'comment',
                            'subtype_id': env.ref('mail.mt_note').id,
                            'body': (
                                '<p><strong>[TEST MODE] Kampanye: %s</strong></p>'
                                '<p>Template: %s (%s)</p>'
                                '<p>%s</p>'
                            ) % (
                                campaign.name,
                                template.name,
                                template.template_name,
                                template.body or '',
                            ),
                            'author_id': author_id,
                        })
                        sent += 1
                    except Exception as e:
                        failed += 1
                        _logger.warning('[Marketing TEST] note fail for partner %s: %s', pid, e)
            else:
                wa_account = env['whatsapp.account'].sudo().browse(wa_account_id)
                company_country = env.company.country_id
                WaMsg = env['whatsapp.message'].sudo()
                has_free_text = 'free_text_json' in WaMsg._fields

                for pid in partner_ids:
                    partner = env['res.partner'].sudo().browse(pid)
                    raw_phone = (partner.mobile or partner.phone or '').strip()
                    if not raw_phone:
                        failed += 1
                        continue

                    if raw_phone.startswith('0'):
                        phone = '+62' + raw_phone[1:]
                    elif raw_phone.isdigit() and len(raw_phone) >= 10:
                        phone = '+' + raw_phone
                    else:
                        try:
                            phone = phone_validation.phone_format(
                                raw_phone,
                                company_country.code,
                                company_country.phone_code,
                            )
                        except Exception:
                            phone = None

                    if not phone:
                        failed += 1
                        continue

                    try:
                        mail_msg = env['mail.message'].sudo().create({
                            'model': 'res.partner',
                            'res_id': pid,
                            'body': template.body or '',
                            'message_type': 'whatsapp_message',
                            'subtype_id': env.ref('mail.mt_note').id,
                            'partner_ids': [Command.link(pid)],
                        })
                        wa_vals = {
                            'mail_message_id': mail_msg.id,
                            'mobile_number': phone,
                            'wa_template_id': template.id,
                            'wa_account_id': wa_account.id,
                        }
                        if has_free_text:
                            wa_vals['free_text_json'] = {}
                        wa_msg = WaMsg.create(wa_vals)
                        wa_msg._send()
                        wa_msg.invalidate_recordset(['state', 'failure_reason'])
                        if wa_msg.state == 'error':
                            failed += 1
                            _logger.warning(
                                '[Marketing] WA error for partner %s: %s',
                                pid, wa_msg.failure_reason or 'unknown',
                            )
                        else:
                            sent += 1
                    except Exception as e:
                        failed += 1
                        _logger.warning('[Marketing] send fail for partner %s: %s', pid, e)

            campaign.write({
                'state': 'done',
                'sent_count': sent,
                'failed_count': failed,
                'matched_count': len(partner_ids),
            })
            cr.commit()
            _logger.info('[Marketing] campaign %s done: sent=%d failed=%d', campaign_id, sent, failed)
    except Exception:
        _logger.exception('[Marketing] broadcast worker fatal error for campaign %s', campaign_id)
        try:
            with odoo_registry(dbname).cursor() as cr2:
                env2 = odoo_api.Environment(cr2, uid, {})
                env2['dke.marketing.campaign'].sudo().browse(campaign_id).write({'state': 'cancelled'})
                cr2.commit()
        except Exception:
            pass

_ALLOWED_IMAGE_MIMETYPES = {'image/jpeg', 'image/png'}
_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB


class MarketingController(http.Controller):
    """REST API endpoints for Marketing campaigns.

    EPIC03 - PBI-6, PBI-7, PBI-8
    """

    # ======================================================================
    # POST /api/marketing/campaigns  (PBI-6)
    # ======================================================================

    @http.route(
        '/api/marketing/campaigns',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_campaign(self, **kwargs):
        """POST /api/marketing/campaigns — Create a draft marketing campaign.

        PBI-6: Validates image (JPG/PNG, max 2 MB), validates that discount_value
        does not exceed the product price, saves the image as a public ir.attachment,
        and creates a dke.marketing.campaign record with state='draft'.

        Request: Multipart/Form-Data
            title           (str, required)
            description     (str)
            product_id      (int)
            discount_type   (str) 'fixed' | 'percent'
            discount_value  (float)
            image_file      (file, JPG/PNG, max 2 MB)
            contact_ids     (str, JSON)  e.g. "[1,2,3]"

        Response 201: { campaign_id, image_url }
        Response 400: { error, message }
        """
        try:
            # ── Required fields ──────────────────────────────────────────
            title = (kwargs.get('title') or '').strip()
            if not title:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Judul kampanye wajib diisi.'},
                    status=400,
                )

            # ── Image validation ─────────────────────────────────────────
            image_file = request.httprequest.files.get('image_file')
            attachment = None
            image_url = None

            if image_file:
                mimetype = image_file.content_type or ''
                if mimetype not in _ALLOWED_IMAGE_MIMETYPES:
                    return request.make_json_response(
                        {
                            'error': 'validation',
                            'message': 'Format gambar harus JPG atau PNG.',
                        },
                        status=400,
                    )

                image_data = image_file.read()
                if len(image_data) > _MAX_IMAGE_BYTES:
                    return request.make_json_response(
                        {
                            'error': 'validation',
                            'message': 'Ukuran gambar tidak boleh melebihi 2 MB.',
                        },
                        status=400,
                    )

                # Save to Odoo filestore as public attachment
                attachment = request.env['ir.attachment'].sudo().create({
                    'name': image_file.filename or 'campaign_image',
                    'mimetype': mimetype,
                    'datas': base64.b64encode(image_data).decode('utf-8'),
                    'public': True,
                    'res_model': 'dke.marketing.campaign',
                })
                base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
                image_url = '{}/web/content/{}/{}'.format(
                    base_url.rstrip('/'),
                    attachment.id,
                    attachment.name,
                )

            # ── Product & discount validation ─────────────────────────────
            raw_product_id = kwargs.get('product_id')
            product = None
            if raw_product_id:
                try:
                    product_id = int(raw_product_id)
                except (ValueError, TypeError):
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'product_id tidak valid.'},
                        status=400,
                    )
                product = request.env['product.product'].sudo().browse(product_id)
                if not product.exists():
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'Produk tidak ditemukan.'},
                        status=400,
                    )

            discount_type = kwargs.get('discount_type') or ''
            raw_discount = kwargs.get('discount_value', 0)
            try:
                discount_value = float(raw_discount)
            except (ValueError, TypeError):
                discount_value = 0.0

            if discount_type == 'fixed' and product and discount_value > product.lst_price:
                return request.make_json_response(
                    {
                        'error': 'validation',
                        'message': (
                            'Nilai diskon (fixed) tidak boleh melebihi harga produk '
                            '({:.2f}).'.format(product.lst_price)
                        ),
                    },
                    status=400,
                )

            if discount_type == 'percent' and not (0 <= discount_value <= 100):
                return request.make_json_response(
                    {
                        'error': 'validation',
                        'message': 'Nilai diskon persentase harus antara 0 dan 100.',
                    },
                    status=400,
                )

            # ── Manual contact selection ──────────────────────────────────
            raw_contact_ids = kwargs.get('contact_ids') or '[]'
            contact_ids = []
            try:
                parsed = json.loads(raw_contact_ids)
                if isinstance(parsed, list):
                    contact_ids = [int(i) for i in parsed if str(i).isdigit() or isinstance(i, int)]
            except (ValueError, TypeError):
                contact_ids = []

            # ── WhatsApp template ─────────────────────────────────────────
            wa_body = (kwargs.get('body') or '').strip()
            lang_code = (kwargs.get('lang_code') or 'id').strip()
            wa_template = None
            wa_submit_warning = None

            if wa_body:
                meta_name = re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_') or 'campaign'
                existing_count = request.env['whatsapp.template'].sudo().search_count(
                    [('template_name', 'like', meta_name)]
                )
                if existing_count:
                    meta_name = '%s_%s' % (meta_name, existing_count)

                wa_account = request.env['whatsapp.account'].sudo().search(
                    [('active', '=', True)], limit=1,
                )
                partner_model = request.env['ir.model'].sudo().search(
                    [('model', '=', 'res.partner')], limit=1,
                )

                wa_template_vals = {
                    'name': title,
                    'template_name': meta_name,
                    'body': wa_body,
                    'lang_code': lang_code,
                    'template_type': 'marketing',
                    'model_id': partner_model.id,
                    'phone_field': 'mobile',
                    'wa_account_id': wa_account.id if wa_account else False,
                    'status': 'draft',
                }
                if attachment:
                    wa_template_vals['header_type'] = 'image'
                    wa_template_vals['header_attachment_ids'] = [(4, attachment.id)]

                wa_template = request.env['whatsapp.template'].sudo().create(wa_template_vals)
                _logger.info(
                    '[Marketing] Created whatsapp.template id=%d name=%s',
                    wa_template.id, meta_name,
                )

                # Auto-submit to Meta
                wa_submit_warning = None
                try:
                    wa_template._compute_variable_ids()
                    wa_template.env.cr.flush()
                    wa_template.invalidate_recordset()
                    wa_template.button_submit_template()
                    wa_template.invalidate_recordset()
                    _logger.info(
                        '[Marketing] Submitted whatsapp.template id=%d to Meta',
                        wa_template.id,
                    )
                except Exception as submit_exc:
                    _logger.warning(
                        '[Marketing] Auto-submit failed for template id=%d: %s',
                        wa_template.id, submit_exc,
                    )
                    wa_submit_warning = (
                        'Template WhatsApp berhasil dibuat, tetapi gagal dikirim ke Meta '
                        'untuk review: {}. Silakan submit manual melalui Odoo.'.format(submit_exc)
                    )

            # ── Create campaign record ────────────────────────────────────
            has_contacts = bool(contact_ids)
            vals = {
                'name': title,
                'description': kwargs.get('description') or '',
                'discount_type': discount_type or False,
                'discount_value': discount_value,
                'state': 'targeted' if has_contacts else 'draft',
                'matched_count': len(contact_ids) if has_contacts else 0,
                'created_by_id': request.env.user.id,
            }
            if product:
                vals['product_id'] = product.id
            if has_contacts:
                vals['target_audience_ids'] = [(6, 0, contact_ids)]
            if attachment:
                vals['image_url'] = image_url
            if wa_template:
                vals['wa_template_id'] = wa_template.id
            if wa_template:
                vals['wa_template_id'] = wa_template.id

            campaign = request.env['dke.marketing.campaign'].sudo().create(vals)

            # Link attachment to campaign record
            if attachment:
                attachment.sudo().write({'res_id': campaign.id})

            response_data = {'campaign_id': campaign.id, 'image_url': image_url}
            if wa_submit_warning:
                response_data['warning'] = wa_submit_warning

            return request.make_json_response(response_data, status=201)
            response_data = {'campaign_id': campaign.id, 'image_url': image_url}
            if wa_submit_warning:
                response_data['warning'] = wa_submit_warning

            return request.make_json_response(response_data, status=201)

        except Exception as exc:
            _logger.exception('[Marketing] create_campaign error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # GET /api/marketing/campaigns  — List all campaigns
    # ======================================================================

    @http.route(
        '/api/marketing/campaigns',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_campaigns(self, **kwargs):
        """GET /api/marketing/campaigns — Retrieve all marketing campaigns.

        Query Params:
            page    (int, default=1)
            limit   (int, default=20, max=100)
            state   (str)  filter by status: draft|targeted|processing|done|cancelled
            search  (str)  filter by campaign title

        Response 200:
        {
            "total": <int>,
            "page": <int>,
            "limit": <int>,
            "campaigns": [
                {
                    "id", "title", "description", "state",
                    "discount_type", "discount_value",
                    "product_id", "product_name",
                    "segment_id", "segment_name",
                    "image_url",
                    "sent_count", "failed_count", "matched_count",
                    "created_by", "create_date"
                }, ...
            ]
        }
        """
        try:
            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)
            offset = (page - 1) * limit
            state_filter = kwargs.get('state', '').strip()
            search_query = kwargs.get('search', '').strip()

            domain = []
            if state_filter:
                domain.append(('state', '=', state_filter))
            if search_query:
                domain.append(('name', 'ilike', search_query))

            Campaign = request.env['dke.marketing.campaign'].sudo()
            total = Campaign.search_count(domain)
            campaigns = Campaign.search(domain, limit=limit, offset=offset, order='create_date desc')

            data = []
            for c in campaigns:
                data.append({
                    'id': c.id,
                    'title': c.name,
                    'description': c.description or '',
                    'state': c.state,
                    'discount_type': c.discount_type or '',
                    'discount_value': c.discount_value,
                    'product_id': c.product_id.id if c.product_id else None,
                    'product_name': c.product_id.name if c.product_id else None,
                    'image_url': c.image_url or None,
                    'sent_count': c.sent_count,
                    'failed_count': c.failed_count,
                    'matched_count': c.matched_count,
                    'created_by': c.created_by_id.name if c.created_by_id else None,
                    'create_date': c.create_date.isoformat() if c.create_date else None,
                    'wa_template_id': c.wa_template_id.id if c.wa_template_id else None,
                    'wa_template_name': c.wa_template_id.name if c.wa_template_id else None,
                    'wa_template_status': c.wa_template_id.status if c.wa_template_id else None,
                    'wa_template_status': c.wa_template_id.status if c.wa_template_id else None,
                })

            return request.make_json_response({
                'total': total,
                'page': page,
                'limit': limit,
                'campaigns': data,
            })

        except Exception as exc:
            _logger.exception('[Marketing] list_campaigns error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # GET /api/marketing/products  — Product list for dropdown
    # ======================================================================

    @http.route(
        '/api/marketing/products',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_products(self, **kwargs):
        """GET /api/marketing/products — Daftar produk untuk dropdown campaign.

        Query Params:
            search  (str)  filter by product name
            limit   (int, default=50, max=200)

        Response 200:
        {
            "products": [
                { "id", "name", "price", "currency" }, ...
            ]
        }
        """
        try:
            search_query = (kwargs.get('search') or '').strip()
            limit = min(int(kwargs.get('limit', 50)), 200)

            domain = [('active', '=', True), ('sale_ok', '=', True)]
            if search_query:
                domain.append(('name', 'ilike', search_query))

            products = request.env['product.product'].sudo().search(
                domain, limit=limit, order='name asc'
            )

            currency = request.env.company.currency_id.name

            data = [
                {
                    'id': p.id,
                    'name': p.name,
                    'price': p.lst_price,
                    'currency': currency,
                }
                for p in products
            ]

            return request.make_json_response({'products': data})

        except Exception as exc:
            _logger.exception('[Marketing] list_products error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # GET /api/marketing/contacts  — Contact search for campaign targeting
    # ======================================================================

    @http.route(
        '/api/marketing/contacts',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_contacts(self, **kwargs):
        """GET /api/marketing/contacts — Cari kontak untuk target campaign.

        Query Params:
            q       (str)  search by name, phone, or mobile
            page    (int, default=1)
            limit   (int, default=20, max=50)

        Response 200:
        {
            "contacts": [{ "id", "name", "phone", "mobile", "email" }, ...],
            "total": int,
            "page": int,
            "limit": int
        }
        """
        try:
            q = (kwargs.get('q') or '').strip()
            page = max(1, int(kwargs.get('page', 1)))
            limit = min(int(kwargs.get('limit', 20)), 50)
            offset = (page - 1) * limit

            domain = [
                ('active', '=', True),
                '|', ('mobile', '!=', False), ('phone', '!=', False),
            ]
            if q:
                domain = [
                    ('active', '=', True),
                    '|', ('mobile', '!=', False), ('phone', '!=', False),
                    '|', '|',
                    ('name', 'ilike', q),
                    ('mobile', 'ilike', q),
                    ('phone', 'ilike', q),
                ]

            total = request.env['res.partner'].sudo().search_count(domain)
            partners = request.env['res.partner'].sudo().search(
                domain, limit=limit, offset=offset, order='name asc'
            )

            contacts = [
                {
                    'id': p.id,
                    'name': p.name,
                    'phone': p.phone or None,
                    'mobile': p.mobile or None,
                    'email': p.email or None,
                }
                for p in partners
            ]

            return request.make_json_response({
                'contacts': contacts,
                'total': total,
                'page': page,
                'limit': limit,
            })

        except Exception as exc:
            _logger.exception('[Marketing] list_contacts error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # GET /api/marketing/wa-templates  — List approved WA templates
    # ======================================================================

    @http.route(
        '/api/marketing/wa-templates',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_wa_templates(self, **kwargs):
        """GET /api/marketing/wa-templates — Approved WA templates (send modal)."""
        try:
            templates = request.env['whatsapp.template'].sudo().search(
                [('status', '=', 'approved')], order='name asc',
            )
            data = [
                {
                    'id': t.id,
                    'name': t.name,
                    'body': t.body or '',
                    'meta_template_name': t.template_name,
                    'lang_code': t.lang_code or 'id',
                    'status': t.status,
                    'category': (t.template_type or 'marketing').upper(),
                }
                for t in templates
            ]
            return request.make_json_response({'templates': data})

        except Exception as exc:
            _logger.exception('[Marketing] list_wa_templates error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # GET /api/marketing/wa-templates/all  — All templates (management page)
    # ======================================================================

    @http.route(
        '/api/marketing/wa-templates/all',
        type='http', auth='user', methods=['GET'], csrf=False, cors='*',
    )
    def list_wa_templates_all(self, **kwargs):
        """GET /api/marketing/wa-templates/all — All WA templates."""
        try:
            templates = request.env['whatsapp.template'].sudo().search([], order='name asc')
            data = [
                {
                    'id': t.id,
                    'name': t.name,
                    'body': t.body or '',
                    'meta_template_name': t.template_name,
                    'lang_code': t.lang_code or 'id',
                    'status': t.status,
                    'category': (t.template_type or 'marketing').upper(),
                    'rejection_reason': t.error_msg or None,
                }
                for t in templates
            ]
            return request.make_json_response({'templates': data})

        except Exception as exc:
            _logger.exception('[Marketing] list_wa_templates_all error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    # ======================================================================
    # POST /api/marketing/wa-templates  — Create + submit to Meta
    # ======================================================================

    @http.route(
        '/api/marketing/wa-templates',
        type='http', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def create_wa_template(self, **kwargs):
        """POST /api/marketing/wa-templates — Create DKE WA template and submit to Meta."""
        try:
            try:
                body = json.loads(request.httprequest.data or b'{}')
            except ValueError:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Request body harus JSON.'}, status=400,
                )

            name = (body.get('name') or '').strip()
            body_text = (body.get('body') or '').strip()
            lang_code = (body.get('lang_code') or 'id').strip()
            category = body.get('category') or 'MARKETING'

            if not name:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Nama template wajib diisi.'}, status=400,
                )
            if not body_text:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Isi pesan wajib diisi.'}, status=400,
                )
            if category not in ('MARKETING', 'UTILITY'):
                category = 'MARKETING'

            # Generate unique Meta template name (lowercase alphanum + underscore)
            meta_name = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'template'
            existing_count = request.env['whatsapp.template'].sudo().search_count(
                [('template_name', 'like', meta_name)]
            )
            if existing_count:
                meta_name = '%s_%s' % (meta_name, existing_count)

            wa_account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            partner_model = request.env['ir.model'].sudo().search(
                [('model', '=', 'res.partner')], limit=1,
            )

            tmpl = request.env['whatsapp.template'].sudo().create({
                'name': name,
                'template_name': meta_name,
                'body': body_text,
                'lang_code': lang_code or 'en',
                'template_type': category.lower(),
                'model_id': partner_model.id,
                'phone_field': 'phone',
                'wa_account_id': wa_account.id if wa_account else False,
                'status': 'draft',
            })

            if wa_account and wa_account.token and wa_account.account_uid:
                meta_url = 'https://graph.facebook.com/v21.0/%s/message_templates' % wa_account.account_uid
                payload = {
                    'name': meta_name,
                    'language': lang_code,
                    'category': category,
                    'components': [{'type': 'BODY', 'text': body_text}],
                }
                try:
                    resp = _requests.post(
                        meta_url,
                        headers={
                            'Authorization': 'Bearer %s' % wa_account.token,
                            'Content-Type': 'application/json',
                        },
                        json=payload,
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        meta_resp = resp.json()
                        tmpl.write({
                            'status': 'pending',
                            'wa_template_uid': str(meta_resp.get('id', '')),
                        })
                    else:
                        _logger.warning('[Marketing] Meta template submit failed: %s', resp.text)
                except _requests.exceptions.RequestException as req_exc:
                    _logger.warning('[Marketing] Meta template submit error: %s', req_exc)
            else:
                _logger.info('[Marketing] No active whatsapp.account — template saved as draft only.')

            return request.make_json_response({
                'id': tmpl.id,
                'name': tmpl.name,
                'body': tmpl.body or '',
                'meta_template_name': tmpl.template_name,
                'lang_code': tmpl.lang_code or 'id',
                'status': tmpl.status,
                'category': (tmpl.template_type or 'marketing').upper(),
                'rejection_reason': tmpl.error_msg or None,
            })

        except Exception as exc:
            _logger.exception('[Marketing] create_wa_template error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500,
            )

    # ======================================================================
    # POST /api/marketing/wa-templates/<id>/refresh-status  — Poll Meta
    # ======================================================================

    @http.route(
        '/api/marketing/wa-templates/<int:template_id>/refresh-status',
        type='http', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def refresh_wa_template_status(self, template_id, **kwargs):
        """POST /api/marketing/wa-templates/{id}/refresh-status — Poll Meta for approval status."""
        try:
            tmpl = request.env['whatsapp.template'].sudo().browse(template_id)
            if not tmpl.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Template tidak ditemukan.'}, status=404,
                )

            wa_account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            if not wa_account or not wa_account.token or not wa_account.account_uid:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'WhatsApp belum dikonfigurasi.'}, status=400,
                )

            meta_url = 'https://graph.facebook.com/v21.0/%s/message_templates' % wa_account.account_uid
            try:
                resp = _requests.get(
                    meta_url,
                    headers={'Authorization': 'Bearer %s' % wa_account.token},
                    params={
                        'name': tmpl.template_name,
                        'fields': 'name,status,rejected_reason',
                    },
                    timeout=15,
                )
            except _requests.exceptions.RequestException as req_exc:
                return request.make_json_response(
                    {'error': 'server_error', 'message': 'Gagal menghubungi Meta: %s' % str(req_exc)},
                    status=502,
                )

            if resp.status_code != 200:
                return request.make_json_response(
                    {'error': 'server_error', 'message': 'Meta API error: %s' % resp.text},
                    status=502,
                )

            STATUS_MAP = {
                'APPROVED': 'approved',
                'REJECTED': 'rejected',
                'PENDING': 'pending',
                'SUBMITTED': 'pending',
            }
            templates_data = resp.json().get('data', [])
            matched = next(
                (t for t in templates_data if t.get('name') == tmpl.template_name), None
            )
            if matched:
                meta_status = matched.get('status', '').upper()
                new_status = STATUS_MAP.get(meta_status, tmpl.status)
                tmpl.write({
                    'status': new_status,
                    'error_msg': matched.get('rejected_reason') or None,
                })

            return request.make_json_response({
                'id': tmpl.id,
                'name': tmpl.name,
                'body': tmpl.body or '',
                'meta_template_name': tmpl.template_name,
                'lang_code': tmpl.lang_code or 'id',
                'status': tmpl.status,
                'category': (tmpl.template_type or 'marketing').upper(),
                'rejection_reason': tmpl.error_msg or None,
            })

        except Exception as exc:
            _logger.exception('[Marketing] refresh_wa_template_status error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500,
            )

    # ======================================================================
    # DELETE /api/marketing/wa-templates/<id>  — Delete template
    # ======================================================================

    @http.route(
        '/api/marketing/wa-templates/<int:template_id>',
        type='http', auth='user', methods=['DELETE'], csrf=False, cors='*',
    )
    def delete_wa_template(self, template_id, **kwargs):
        """DELETE /api/marketing/wa-templates/{id} — Delete a DKE WA template."""
        try:
            tmpl = request.env['whatsapp.template'].sudo().browse(template_id)
            if not tmpl.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Template tidak ditemukan.'}, status=404,
                )
            tmpl.unlink()
            return request.make_json_response({'status': 'ok'})

        except Exception as exc:
            _logger.exception('[Marketing] delete_wa_template error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500,
            )

    # ======================================================================
    # GET /api/marketing/campaigns/<int:campaign_id>  — Single campaign
    # ======================================================================

    @http.route(
        '/api/marketing/campaigns/<int:campaign_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def get_campaign(self, campaign_id, **kwargs):
        """GET /api/marketing/campaigns/{id} — Return a single campaign record."""
        try:
            campaign = request.env['dke.marketing.campaign'].sudo().browse(campaign_id)
            if not campaign.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Kampanye tidak ditemukan.'},
                    status=404,
                )

            c = campaign

            # Auto-sync template status via direct Meta API if still pending/draft
            if c.wa_template_id and c.wa_template_id.status not in ('approved', 'rejected'):
                try:
                    wa_account = request.env['whatsapp.account'].sudo().search(
                        [('active', '=', True)], limit=1,
                    )
                    if wa_account and wa_account.token and wa_account.account_uid:
                        meta_url = 'https://graph.facebook.com/v21.0/%s/message_templates' % wa_account.account_uid
                        resp = _requests.get(
                            meta_url,
                            headers={'Authorization': 'Bearer %s' % wa_account.token},
                            params={
                                'name': c.wa_template_id.template_name,
                                'fields': 'name,status,rejected_reason',
                            },
                            timeout=10,
                        )
                        if resp.status_code == 200:
                            STATUS_MAP = {
                                'APPROVED': 'approved',
                                'REJECTED': 'rejected',
                                'PENDING': 'pending',
                                'SUBMITTED': 'pending',
                            }
                            matched = next(
                                (t for t in resp.json().get('data', [])
                                 if t.get('name') == c.wa_template_id.template_name),
                                None,
                            )
                            if matched:
                                new_status = STATUS_MAP.get(matched.get('status', '').upper(), c.wa_template_id.status)
                                c.wa_template_id.write({
                                    'status': new_status,
                                    'error_msg': matched.get('rejected_reason') or None,
                                })
                                _logger.info(
                                    '[Marketing] Auto-synced template id=%d status=%s',
                                    c.wa_template_id.id, new_status,
                                )
                except Exception as sync_exc:
                    _logger.warning('[Marketing] Auto-sync template status failed: %s', sync_exc)

            return request.make_json_response({
                'id': c.id,
                'title': c.name,
                'description': c.description or '',
                'state': c.state,
                'discount_type': c.discount_type or '',
                'discount_value': c.discount_value,
                'product_id': c.product_id.id if c.product_id else None,
                'product_name': c.product_id.name if c.product_id else None,
                'segment_id': c.segment_id.id if c.segment_id else None,
                'segment_name': c.segment_id.name if c.segment_id else None,
                'image_url': c.image_url or None,
                'sent_count': c.sent_count,
                'failed_count': c.failed_count,
                'matched_count': c.matched_count,
                'created_by': c.created_by_id.name if c.created_by_id else None,
                'create_date': c.create_date.isoformat() if c.create_date else None,
                'wa_template_id': c.wa_template_id.id if c.wa_template_id else None,
                'wa_template_name': c.wa_template_id.name if c.wa_template_id else None,
                'wa_template_status': c.wa_template_id.status if c.wa_template_id else None,
                'wa_template_status': c.wa_template_id.status if c.wa_template_id else None,
            })

        except Exception as exc:
            _logger.exception('[Marketing] get_campaign error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    @http.route('/api/marketing/campaigns/<int:campaign_id>/send', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def send_campaign(self, campaign_id, **kwargs):
        """POST /api/marketing/campaigns/{id}/send — Broadcast via Meta Graph API.

        PBI-8: Uses whatsapp.template + whatsapp.account credentials to call
        Meta Graph API directly — no dependency on Odoo's whatsapp.message model.
        """
        try:
            try:
                body = json.loads(request.httprequest.data or b'{}')
            except ValueError:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Request body harus JSON.'},
                    status=400,
                )

            raw_template_id = body.get('wa_template_id')
            if raw_template_id is not None:
                try:
                    raw_template_id = int(raw_template_id)
                except (TypeError, ValueError):
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'wa_template_id tidak valid.'},
                        status=400,
                    )

            test_mode = bool(body.get('test_mode', False))

            campaign = request.env['dke.marketing.campaign'].sudo().browse(campaign_id)
            if not campaign.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Kampanye tidak ditemukan.'},
                    status=404,
                )

            if campaign.state == 'processing':
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Kampanye sedang dalam proses pengiriman.'},
                    status=400,
                )

            if campaign.state not in ('draft', 'targeted'):
                return request.make_json_response(
                    {
                        'error': 'validation',
                        'message': 'Kampanye harus berada dalam status Draft atau Tersegmentasi sebelum dapat dikirim.',
                    },
                    status=400,
                )

            wa_template_id = raw_template_id or (campaign.wa_template_id.id if campaign.wa_template_id else None)
            if not wa_template_id:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Kampanye belum memiliki template WhatsApp.'},
                    status=400,
                )

            template = request.env['whatsapp.template'].sudo().browse(wa_template_id)
            if not template.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Template WhatsApp tidak ditemukan.'},
                    status=404,
                )

            if not test_mode and template.status != 'approved':
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Template harus berstatus Approved.'},
                    status=400,
                )

            # Get WA credentials — skipped in test_mode
            wa_account = None
            if not test_mode:
                wa_account = request.env['whatsapp.account'].sudo().search(
                    [('active', '=', True)], limit=1,
                )
                if not wa_account or not wa_account.token or not wa_account.phone_uid:
                    return request.make_json_response(
                        {
                            'error': 'validation',
                            'message': 'WhatsApp belum dikonfigurasi. Atur koneksi di halaman Integrasi terlebih dahulu.',
                        },
                        status=400,
                    )

            scope = body.get('scope', 'all')
            if scope not in ('all', 'targeted'):
                scope = 'all'

            if scope == 'all':
                partners = request.env['res.partner'].sudo().search([
                    ('active', '=', True),
                    '|', ('mobile', '!=', False), ('phone', '!=', False),
                ])
            else:
                partners = campaign.target_audience_ids

            if not partners:
                no_partner_msg = (
                    'Tidak ada penerima dengan nomor telepon yang valid.'
                    if scope == 'all'
                    else 'Kampanye belum memiliki target pelanggan. Tambahkan kontak terlebih dahulu saat membuat kampanye.'
                )
                return request.make_json_response(
                    {'error': 'validation', 'message': no_partner_msg},
                    status=400,
                )

            partner_ids = partners.ids
            dbname = request.env.cr.dbname
            uid = request.env.uid

            # Mark as processing and commit so the background thread sees the state.
            campaign.write({
                'wa_template_id': template.id,
                'state': 'processing',
                'matched_count': len(partner_ids),
                'sent_count': 0,
                'failed_count': 0,
            })
            request.env.cr.commit()

            t = threading.Thread(
                target=_marketing_broadcast_worker,
                args=(dbname, uid, campaign_id, partner_ids, template.id,
                      wa_account.id if wa_account else None, test_mode),
                daemon=True,
            )
            t.start()

            return request.make_json_response(
                {'status': 'processing', 'queued': len(partner_ids)},
                status=202,
            )

        except Exception as exc:
            _logger.exception('[Marketing] send_campaign error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    @http.route('/api/marketing/campaigns/<int:campaign_id>/status', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_campaign_status(self, campaign_id, **kwargs):
        """GET /api/marketing/campaigns/{id}/status — Get broadcast progress.

        PBI-8: Returns state, sent_count, failed_count, matched_count.
        """
        try:
            campaign = request.env['dke.marketing.campaign'].sudo().browse(campaign_id)
            if not campaign.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Kampanye tidak ditemukan.'},
                    status=404,
                )

            return request.make_json_response({
                'state': campaign.state,
                'sent_count': campaign.sent_count,
                'failed_count': campaign.failed_count,
                'matched_count': campaign.matched_count,
            })

        except Exception as exc:
            _logger.exception('[Marketing] get_campaign_status error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )
