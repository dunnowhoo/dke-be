# -*- coding: utf-8 -*-

import base64
import json
import logging
import re

from odoo import http, Command
from odoo.http import request
from odoo.addons.phone_validation.tools import phone_validation

_logger = logging.getLogger(__name__)

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
            target_segment  (int)  segment_id

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

            # ── Target segment ────────────────────────────────────────────
            raw_segment = kwargs.get('target_segment')
            segment_id = False
            if raw_segment:
                try:
                    segment_id = int(raw_segment)
                except (ValueError, TypeError):
                    segment_id = False

            # ── Create campaign record ────────────────────────────────────
            vals = {
                'name': title,
                'description': kwargs.get('description') or '',
                'discount_type': discount_type or False,
                'discount_value': discount_value,
                'state': 'draft',
                'created_by_id': request.env.user.id,
            }
            if product:
                vals['product_id'] = product.id
            if segment_id:
                vals['segment_id'] = segment_id
            if attachment:
                vals['image_url'] = image_url

            campaign = request.env['dke.marketing.campaign'].sudo().create(vals)

            # Link attachment to campaign record
            if attachment:
                attachment.sudo().write({'res_id': campaign.id})

            return request.make_json_response(
                {'campaign_id': campaign.id, 'image_url': image_url},
                status=201,
            )

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
            })

        except Exception as exc:
            _logger.exception('[Marketing] get_campaign error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)},
                status=500,
            )

    @http.route('/api/marketing/segmentation/preview', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def preview_segmentation(self, **kwargs):
        """POST /api/marketing/segmentation/preview — Preview customer segment.

        PBI-7: Translates filter rules to SQL, returns matched count and sample.
        Request Body: { "rules": [{ "field": "...", "operator": "...", "value": ... }] }
        """
        # TODO: Implement dynamic segmentation query
        return request.make_json_response({'status': 'ok', 'matched_count': 0, 'sample': []})

    @http.route('/api/marketing/campaigns/<int:campaign_id>/target', type='http', auth='user', methods=['PUT'], csrf=False, cors='*')
    def save_target(self, campaign_id, **kwargs):
        """PUT /api/marketing/campaigns/{id}/target — Save segmentation rules.

        PBI-7: Saves rules to campaign.
        """
        # TODO: Implement target saving
        return request.make_json_response({'status': 'ok'})

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
                partners = (
                    campaign.segment_id.preview_partner_ids
                    if campaign.segment_id and campaign.segment_id.preview_partner_ids
                    else campaign.target_audience_ids
                )

            if not partners:
                no_partner_msg = (
                    'Tidak ada penerima dengan nomor telepon yang valid.'
                    if scope == 'all'
                    else 'Kampanye belum memiliki target pelanggan. Gunakan opsi Semua Pelanggan atau tambahkan segmen terlebih dahulu.'
                )
                return request.make_json_response(
                    {'error': 'validation', 'message': no_partner_msg},
                    status=400,
                )

            company_country = request.env.company.country_id
            sent = 0
            failed = 0

            if test_mode:
                _logger.info(
                    '[Marketing TEST MODE] Campaign %s — would send to %d partners via template "%s"',
                    campaign_id, len(partners), template.template_name,
                )
                for partner in partners:
                    try:
                        request.env['mail.message'].sudo().create({
                            'model': 'res.partner',
                            'res_id': partner.id,
                            'message_type': 'comment',
                            'subtype_id': request.env.ref('mail.mt_note').id,
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
                            'author_id': request.env.user.partner_id.id,
                        })
                        sent += 1
                    except Exception as note_exc:
                        failed += 1
                        _logger.warning('[Marketing TEST MODE] Could not create note for partner %s: %s', partner.id, note_exc)
            else:
                for partner in partners:
                    raw_phone = partner.mobile or partner.phone
                    if not raw_phone:
                        failed += 1
                        continue

                    try:
                        formatted = phone_validation.phone_format(
                            raw_phone,
                            company_country.code,
                            company_country.phone_code,
                        )
                    except Exception:
                        formatted = None

                    if not formatted:
                        failed += 1
                        continue

                    # Odoo whatsapp.message stores mobile_number without leading '+'
                    phone_no_plus = formatted.lstrip('+')

                    try:
                        mail_msg = request.env['mail.message'].sudo().create({
                            'model': 'res.partner',
                            'res_id': partner.id,
                            'body': template.body or '',
                            'message_type': 'whatsapp_message',
                            'subtype_id': request.env.ref('mail.mt_note').id,
                            'partner_ids': [Command.link(partner.id)],
                        })
                        wa_msg = request.env['whatsapp.message'].sudo().create({
                            'mail_message_id': mail_msg.id,
                            'mobile_number': phone_no_plus,
                            'wa_template_id': template.id,
                            'wa_account_id': wa_account.id,
                        })
                        wa_msg._send()
                        wa_msg.invalidate_recordset(['state', 'failure_reason'])
                        if wa_msg.state == 'error':
                            failed += 1
                            _logger.warning(
                                '[Marketing] WA message error for partner %s: %s',
                                partner.id, wa_msg.failure_reason or 'unknown',
                            )
                        else:
                            sent += 1
                    except Exception as partner_exc:
                        failed += 1
                        _logger.warning('[Marketing] Failed to send to partner %s: %s', partner.id, partner_exc)

                if sent == 0 and failed > 0:
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'Semua pesan gagal dikirim. Periksa nomor telepon dan konfigurasi WhatsApp.'},
                        status=400,
                    )

            campaign.write({
                'wa_template_id': template.id,
                'state': 'processing',
                'sent_count': sent,
                'failed_count': failed,
                'matched_count': len(partners),
            })

            return request.make_json_response({'status': 'ok', 'queued': sent})

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
