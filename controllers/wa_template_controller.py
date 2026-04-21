# -*- coding: utf-8 -*-

import json
import logging
import re
from datetime import datetime

from pytz import timezone, utc

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def _sync_template_variables(template):
    """Ensure whatsapp.template.variable records exist for all {{n}} in body/header.

    Odoo's button_submit_template() needs variable_ids populated to build a
    valid WhatsApp API payload. Without these records the API call is malformed
    and returns "query was malformed".
    """
    WaVar = template.env['whatsapp.template.variable'].sudo()
    existing = set((v.name, v.line_type) for v in template.variable_ids)

    to_create = []

    # Body variables — {{1}}, {{2}}, …
    for var_name in sorted(set(re.findall(r'\{\{\d+\}\}', template.body or ''))):
        if (var_name, 'body') not in existing:
            to_create.append({
                'wa_template_id': template.id,
                'name': var_name,
                'line_type': 'body',
                'field_type': 'free_text',
                'demo_value': 'Contoh Nilai',
            })

    # Header text variables
    if template.header_type == 'text':
        for var_name in sorted(set(re.findall(r'\{\{\d+\}\}', template.header_text or ''))):
            if (var_name, 'header') not in existing:
                to_create.append({
                    'wa_template_id': template.id,
                    'name': var_name,
                    'line_type': 'header',
                    'field_type': 'free_text',
                    'demo_value': 'Contoh Nilai',
                })

    if to_create:
        WaVar.create(to_create)


def _set_unique_demo_values(template):
    """Assign unique, numbered demo values to every variable so Meta API accepts the submission.

    Meta WhatsApp API rejects template submissions where multiple variables share
    an identical demo value (e.g. all "Contoh Nilai"). We assign:
      - Body vars  → "ContohNilai1", "ContohNilai2", … (sorted by variable name)
      - Header var → "ContohHeader"

    This must be called AFTER _sync_template_variables() so all variable records exist.
    """
    template.invalidate_recordset()
    body_vars = sorted(
        [v for v in template.variable_ids if v.line_type == 'body'],
        key=lambda v: v.name,
    )
    header_vars = [v for v in template.variable_ids if v.line_type == 'header']

    for idx, var in enumerate(body_vars, start=1):
        var.write({'demo_value': 'ContohNilai%d' % idx})
    for var in header_vars:
        var.write({'demo_value': 'ContohHeader'})

    template.env.cr.flush()
    template.invalidate_recordset()


def _require_sales_access():
    """Return error dict if user is not Sales Staff, Sales Manager, or Admin."""
    user = request.env.user
    if not (
        user.has_group('dke_crm.group_sales_staff')
        or user.has_group('dke_crm.group_sales_manager')
        or user._is_admin()
    ):
        return _error(403, 'Akses ditolak. Hanya Sales Staff atau Manager yang dapat mengakses.')
    return None


def _serialize_template(t):
    """Serialize a whatsapp.template record to dict."""
    variables = []
    for v in t.variable_ids:
        variables.append({
            'id': v.id,
            'name': v.name,
            'line_type': v.line_type,
            'field_type': v.field_type,
            'field_name': v.field_name or '',
            'demo_value': v.demo_value or '',
        })

    buttons = []
    for b in t.button_ids:
        buttons.append({
            'id': b.id,
            'name': b.name,
            'button_type': b.button_type,
            'url_type': b.url_type or '',
            'website_url': b.website_url or '',
            'call_number': b.call_number or '',
        })

    return {
        'id': t.id,
        'name': t.name,
        'template_name': t.template_name or '',
        'status': t.status or 'draft',
        'header_type': t.header_type or 'none',
        'header_text': t.header_text or '',
        'body': t.body or '',
        'footer_text': t.footer_text or '',
        'template_type': t.template_type or 'utility',
        'lang_code': t.lang_code or 'en_US',
        'quality': t.quality or '',
        'wa_account_id': t.wa_account_id.id if t.wa_account_id else None,
        'wa_account_name': t.wa_account_id.name if t.wa_account_id else '',
        'model': t.model or '',
        'phone_field': t.phone_field or '',
        'active': t.active,
        'variables': variables,
        'buttons': buttons,
        'created_at': t.create_date.isoformat() if t.create_date else None,
        'updated_at': t.write_date.isoformat() if t.write_date else None,
    }


class WaTemplateController(http.Controller):
    """REST API endpoints for WhatsApp Template management."""

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/list — List all templates             #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/list',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def list_templates(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        params = kwargs if kwargs else {}
        page = int(params.get('page', 1))
        limit = int(params.get('limit', 20))
        status_filter = params.get('status', '')

        domain = []
        if status_filter:
            domain.append(('status', '=', status_filter))

        WaTemplate = request.env['whatsapp.template'].sudo()
        total = WaTemplate.search_count(domain)
        templates = WaTemplate.search(
            domain,
            order='create_date desc',
            limit=limit,
            offset=(page - 1) * limit,
        )

        return {
            'status': 'success',
            'data': [_serialize_template(t) for t in templates],
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'total_pages': max(1, -(-total // limit)),
            },
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/create — Create a new template       #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/create',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def create_template(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        params = kwargs if kwargs else {}
        name = (params.get('name') or '').strip()
        template_name = (params.get('template_name') or '').strip()
        body = (params.get('body') or '').strip()
        header_type = params.get('header_type', 'none')
        header_text = (params.get('header_text') or '').strip()
        footer_text = (params.get('footer_text') or '').strip()
        template_type = params.get('template_type', 'utility')
        lang_code = params.get('lang_code', 'en_US')
        wa_account_id = params.get('wa_account_id')

        if not name:
            return _error(400, 'Nama template wajib diisi.')
        if not template_name:
            return _error(400, 'Nama template WhatsApp wajib diisi (huruf kecil, underscore).')
        if not body:
            return _error(400, 'Body template wajib diisi.')

        # Validate wa_account_id
        if not wa_account_id:
            accounts = request.env['whatsapp.account'].sudo().search([], limit=1)
            if accounts:
                wa_account_id = accounts[0].id
            else:
                return _error(400, 'Belum ada WhatsApp Account yang dikonfigurasi.')
        else:
            account = request.env['whatsapp.account'].sudo().browse(int(wa_account_id))
            if not account.exists():
                return _error(400, 'WhatsApp Account tidak ditemukan.')

        # model_id is required by Odoo — default to res.partner
        IrModel = request.env['ir.model'].sudo()
        partner_model = IrModel.search([('model', '=', 'res.partner')], limit=1)

        vals = {
            'name': name,
            'template_name': template_name,
            'body': body,
            'header_type': header_type,
            'header_text': header_text if header_type in ('text',) else '',
            'footer_text': footer_text,
            'template_type': template_type,
            'lang_code': lang_code,
            'wa_account_id': int(wa_account_id),
            'model_id': partner_model.id,
            'phone_field': 'mobile',
            'status': 'draft',
        }

        try:
            template = request.env['whatsapp.template'].sudo().create(vals)
        except Exception as e:
            _logger.error('WA template creation failed: %s', e, exc_info=True)
            return _error(500, 'Gagal membuat template: %s' % str(e))

        return {
            'status': 'success',
            'data': _serialize_template(template),
            'message': 'Template berhasil dibuat.',
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/detail/<id> — Get template detail    #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/detail/<int:template_id>',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def detail_template(self, template_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        template = request.env['whatsapp.template'].sudo().browse(template_id)
        if not template.exists():
            return _error(404, 'Template tidak ditemukan.')

        return {
            'status': 'success',
            'data': _serialize_template(template),
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/update/<id> — Update template        #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/update/<int:template_id>',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def update_template(self, template_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        template = request.env['whatsapp.template'].sudo().browse(template_id)
        if not template.exists():
            return _error(404, 'Template tidak ditemukan.')

        if template.status != 'draft':
            return _error(400, 'Hanya template berstatus draft yang dapat diedit.')

        params = kwargs if kwargs else {}
        vals = {}

        if 'name' in params:
            vals['name'] = (params['name'] or '').strip()
        if 'template_name' in params:
            vals['template_name'] = (params['template_name'] or '').strip()
        if 'body' in params:
            vals['body'] = (params['body'] or '').strip()
        if 'header_type' in params:
            vals['header_type'] = params['header_type']
        if 'header_text' in params:
            vals['header_text'] = (params['header_text'] or '').strip()
        if 'footer_text' in params:
            vals['footer_text'] = (params['footer_text'] or '').strip()
        if 'template_type' in params:
            vals['template_type'] = params['template_type']
        if 'lang_code' in params:
            vals['lang_code'] = params['lang_code']

        if not vals:
            return _error(400, 'Tidak ada field yang diubah.')

        try:
            template.write(vals)
        except Exception as e:
            _logger.error('WA template update failed: %s', e, exc_info=True)
            return _error(500, 'Gagal memperbarui template: %s' % str(e))

        return {
            'status': 'success',
            'data': _serialize_template(template),
            'message': 'Template berhasil diperbarui.',
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/delete/<id> — Delete template        #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/delete/<int:template_id>',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def delete_template(self, template_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        template = request.env['whatsapp.template'].sudo().browse(template_id)
        if not template.exists():
            return _error(404, 'Template tidak ditemukan.')

        name = template.name
        try:
            template.unlink()
        except Exception as e:
            _logger.error('WA template delete failed: %s', e, exc_info=True)
            return _error(500, 'Gagal menghapus template: %s' % str(e))

        return {
            'status': 'success',
            'message': 'Template "%s" berhasil dihapus.' % name,
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/submit/<id> — Submit for Meta approval #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/submit/<int:template_id>',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def submit_template(self, template_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        template = request.env['whatsapp.template'].sudo().browse(template_id)
        if not template.exists():
            return _error(404, 'Template tidak ditemukan.')

        if template.status not in ('draft',):
            return _error(400, 'Template hanya bisa di-submit saat berstatus draft.')

        # Ensure variable_ids are populated before submitting to WhatsApp API.
        # variable_ids is a stored computed field (precompute=True). When the template
        # is created via sudo() ORM, the compute may not have fired yet, or its ORM
        # cache may be stale. We:
        #   1. Explicitly trigger the native compute so Odoo creates the records.
        #   2. Flush pending writes (so the new records are persisted to DB).
        #   3. Invalidate the recordset cache so button_submit_template() reads fresh data.
        #   4. As a safety net, _sync_template_variables fills any remaining gaps.
        template._compute_variable_ids()
        template.env.cr.flush()          # flush ORM write queue to DB
        template.invalidate_recordset()  # clear stale ORM cache
        _sync_template_variables(template)
        template.invalidate_recordset()  # clear cache again after _sync writes

        # Meta WhatsApp API requires each variable to have a UNIQUE, non-generic
        # demo value. All variables with the same value (e.g. "Contoh Nilai")
        # cause Meta to reject the submission as "query was malformed".
        _set_unique_demo_values(template)

        try:
            template.button_submit_template()
        except Exception as e:
            _logger.error('WA template submit failed: %s', e, exc_info=True)
            return _error(500, 'Gagal submit template: %s' % str(e))

        template.invalidate_recordset()
        return {
            'status': 'success',
            'data': _serialize_template(template),
            'message': 'Template telah di-submit untuk approval Meta.',
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/accounts — List WhatsApp accounts    #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/accounts',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def list_accounts(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        accounts = request.env['whatsapp.account'].sudo().search([('active', '=', True)])
        data = [{
            'id': a.id,
            'name': a.name,
            'phone_uid': a.phone_uid or '',
        } for a in accounts]

        return {
            'status': 'success',
            'data': data,
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/contacts — List contacts (partners + chat)     #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/contacts',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def list_contacts(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        params = kwargs if kwargs else {}
        search_term = (params.get('search') or '').strip()
        page = int(params.get('page', 1))
        limit = int(params.get('limit', 50))

        Partner = request.env['res.partner'].sudo()
        ChatRoom = request.env['dke.chat.room'].sudo()

        # Gather partner IDs from chat rooms (these are contacts we chatted with)
        chat_partner_ids = ChatRoom.search([
            ('customer_id', '!=', False),
        ]).mapped('customer_id').ids

        # Build domain: partners with phone/mobile OR partners from chat rooms
        domain = [
            '|',
            '|',
            '&', ('mobile', '!=', False), ('mobile', '!=', ''),
            '&', ('phone', '!=', False), ('phone', '!=', ''),
            ('id', 'in', chat_partner_ids),
        ]
        if search_term:
            domain = ['&'] + domain + ['|', '|', '|',
                ('name', 'ilike', search_term),
                ('mobile', 'ilike', search_term),
                ('phone', 'ilike', search_term),
                ('email', 'ilike', search_term),
            ]

        total = Partner.search_count(domain)
        partners = Partner.search(
            domain,
            order='name asc',
            limit=limit,
            offset=(page - 1) * limit,
        )

        # Also include chat-room-only contacts (rooms with customer_name but no partner)
        orphan_rooms = ChatRoom.search([
            ('customer_id', '=', False),
            ('customer_name', '!=', False),
            ('customer_name', '!=', ''),
        ])
        orphan_data = []
        for room in orphan_rooms:
            name = room.customer_name or 'Unknown'
            # Search for existing partner — do NOT auto-create to avoid
            # unintended side effects on a read/list endpoint.
            existing = Partner.search([('name', '=', name)], limit=1)
            if not existing:
                # Skip rooms with no partner — do not create here
                continue
            # Silently link if found but not yet linked
            if existing.id not in [p.id for p in partners]:
                if not search_term or search_term.lower() in name.lower():
                    orphan_data.append({
                        'id': existing.id,
                        'name': existing.name or '',
                        'mobile': existing.mobile or existing.phone or '',
                        'email': existing.email or '',
                    })

        data = [{
            'id': p.id,
            'name': p.name or '',
            'mobile': p.mobile or p.phone or '',
            'email': p.email or '',
        } for p in partners] + orphan_data

        # Deduplicate by normalised phone number to prevent the same physical person
        # appearing twice (e.g. one res.partner created manually with formatted phone
        # "+62 812-5581-2675" and another created from WhatsApp with "+6281255812675").
        # Normalisation: keep digits only, strip leading country code 62 → use last 9 digits
        # as the dedup key so "08123456789" == "+628123456789" == "628123456789".
        def _phone_key(mobile: str) -> str:
            digits = ''.join(c for c in (mobile or '') if c.isdigit())
            return digits[-9:] if len(digits) >= 9 else digits

        seen_phones: set = set()
        seen_ids: set = set()
        deduped = []
        for entry in data:
            eid = entry['id']
            key = _phone_key(entry.get('mobile', ''))
            # Always keep entry if no phone (id-only dedup); skip if phone already seen
            if eid in seen_ids:
                continue
            if key and key in seen_phones:
                continue
            seen_ids.add(eid)
            if key:
                seen_phones.add(key)
            deduped.append(entry)

        total = len(deduped)

        return {
            'status': 'success',
            'data': deduped,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'total_pages': max(1, -(-total // limit)),
            },
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/<id>/send — Send template to contacts #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/send/<int:template_id>',
        type='json', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def send_template(self, template_id, **kwargs):
        """Send a WA template to selected contacts immediately or scheduled.

        Body JSON:
        {
            "contact_ids": [1, 2, 3],
            "send_at": "2026-04-20 10:00:00",   // optional — schedule
            "variable_values": {"1": "Agus", "2": "ORD-001", "3": "https://..."}  // optional
        }
        """
        err = _require_sales_access()
        if err:
            return err

        template = request.env['whatsapp.template'].sudo().browse(template_id)
        if not template.exists():
            return _error(404, 'Template tidak ditemukan.')
        if template.status != 'approved':
            return _error(400, 'Template harus berstatus approved untuk dikirim.')

        params = kwargs if kwargs else {}
        contact_ids = params.get('contact_ids', [])
        send_at_raw = params.get('send_at')
        variable_values = params.get('variable_values', {})

        # Convert send_at from user's local timezone to UTC
        send_at = None
        if send_at_raw:
            try:
                user_tz = timezone(request.env.user.tz or 'Asia/Jakarta')
                # Accept both 'YYYY-MM-DD HH:MM:SS' and ISO 'YYYY-MM-DDTHH:MM:SS' formats
                raw_str = str(send_at_raw).strip().replace('T', ' ')[:19]
                naive_dt = datetime.strptime(raw_str, '%Y-%m-%d %H:%M:%S')
                local_dt = user_tz.localize(naive_dt)
                send_at = local_dt.astimezone(utc).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                send_at = send_at_raw  # fallback: use as-is

        if not contact_ids:
            return _error(400, 'Pilih minimal 1 kontak.')

        partners = request.env['res.partner'].sudo().browse(contact_ids)
        ChatRoom = request.env['dke.chat.room'].sudo()
        # Accept partners that have phone/mobile OR already have a chat room
        chat_partner_ids = set(
            ChatRoom.search([('customer_id', 'in', contact_ids)]).mapped('customer_id').ids
        )
        valid_partners = partners.filtered(
            lambda p: p.exists() and (p.mobile or p.phone or p.id in chat_partner_ids)
        )
        if not valid_partners:
            return _error(400, 'Tidak ada kontak valid.')

        # Build body with variable substitution
        wa_body = template.body or template.name or ''
        if variable_values and isinstance(variable_values, dict):
            for key, value in variable_values.items():
                wa_body = wa_body.replace('{{%s}}' % str(key), str(value))
        ScheduledMsg = request.env['dke.scheduled.message'].sudo()

        sent_count = 0
        scheduled_count = 0
        errors = []

        for partner in valid_partners:
            # Isolate each partner in a savepoint so a failure for one contact
            # never rolls back the ORM writes for contacts already processed.
            try:
                with request.env.cr.savepoint():
                    # Search for existing room: first by customer_id (res.partner link),
                    # then by external_conversation_id (phone number) as fallback so we
                    # never create a duplicate room for a customer who already has one.
                    room = ChatRoom.search([('customer_id', '=', partner.id)], limit=1)
                    if not room:
                        phone_raw = (partner.mobile or partner.phone or '').strip()
                        if phone_raw:
                            # Normalize: strip all non-digit chars then apply country code.
                            # Handles formats like '+62 812-5581-2675', '08123456789', etc.
                            had_plus = phone_raw.startswith('+')
                            phone_digits = ''.join(c for c in phone_raw if c.isdigit())
                            if phone_digits.startswith('0'):
                                phone_int = '+62' + phone_digits[1:]
                            elif had_plus or phone_digits.startswith('62'):
                                phone_int = '+' + phone_digits
                            else:
                                phone_int = '+62' + phone_digits
                            # Try exact international format first, then suffix match
                            room = ChatRoom.search(
                                [('external_conversation_id', '=', phone_int)], limit=1
                            )
                            if not room and len(phone_digits) >= 8:
                                room = ChatRoom.search(
                                    [('external_conversation_id', 'like', phone_digits[-8:])],
                                    limit=1,
                                )
                    if not room:
                        room = ChatRoom.create({
                            'name': 'Chat - %s' % partner.name,
                            'customer_id': partner.id,
                            'state': 'active',
                        })

                    now = fields.Datetime.now()
                    msg_vals = {
                        'chat_room_id': room.id,
                        'customer_id': partner.id,
                        'created_by_id': request.env.uid,
                        'message': wa_body,
                        'schedule_type': 'manual',
                        'wa_template_id': template.id,
                        'variable_values': json.dumps(variable_values) if variable_values else '',
                    }

                    if send_at:
                        msg_vals['send_at'] = send_at
                        msg_vals['state'] = 'pending'
                        ScheduledMsg.create(msg_vals)
                        scheduled_count += 1
                    else:
                        # Immediate send.
                        # CRITICAL: set state='sent' BEFORE calling the WA API so
                        # the cron (which picks up state='pending') can NEVER
                        # double-send this record even if the API call is slow.
                        msg_vals['send_at'] = now
                        msg_vals['state'] = 'sent'
                        msg_vals['sent_at'] = now
                        sched_rec = ScheduledMsg.create(msg_vals)

                        # Call WA API
                        wa_sent = sched_rec._send_via_whatsapp_template(sched_rec, room)

                        if not wa_sent:
                            sched_rec.write({
                                'state': 'failed',
                                'error_message': 'WA API tidak tersedia — pesan direkam di chat',
                            })

                        # ALWAYS create dke.chat.message (NEVER channel.message_post).
                        # channel.message_post creates a mail.message which the frontend
                        # cannot read — it only reads dke.chat.message. Using
                        # channel.message_post for rooms with discuss_channel_id causes
                        # the bubble to appear blue (CS agent) instead of yellow (auto).
                        # session_id=False ensures this message is NEVER linked to a CC
                        # session and never affects session evaluation metrics.
                        request.env['dke.chat.message'].sudo().create({
                            'room_id': room.id,
                            'session_id': False,
                            'sender_type': 'system',
                            'sender_id': request.env.uid,
                            'content_text': wa_body,
                            'message_type': 'text',
                            'is_automated': True,
                            'send_status': 'sent' if wa_sent else 'failed',
                            'created_at': now,
                        })

                        room.write({'last_message_time': now})
                        if wa_sent:
                            sent_count += 1
                        else:
                            errors.append({
                                'contact_id': partner.id,
                                'name': partner.name,
                                'error': 'WA API tidak tersedia — pesan direkam di chat',
                            })

            except Exception as e:
                _logger.error('Send template to partner %s failed: %s', partner.id, e, exc_info=True)
                errors.append({'contact_id': partner.id, 'name': partner.name, 'error': str(e)})
        result = {
            'status': 'success',
            'sent_count': sent_count,
            'scheduled_count': scheduled_count,
            'error_count': len(errors),
        }
        if send_at:
            result['message'] = '%d pesan dijadwalkan.' % scheduled_count
        elif sent_count > 0 and len(errors) == 0:
            result['message'] = '%d pesan berhasil dikirim.' % sent_count
        elif sent_count > 0 and len(errors) > 0:
            result['message'] = '%d pesan dikirim, %d gagal (direkam di chat).' % (sent_count, len(errors))
        else:
            result['message'] = 'WA API tidak tersedia. %d pesan direkam di riwayat chat (tidak terkirim ke WhatsApp).' % len(errors)
        if errors:
            result['errors'] = errors
        return result

