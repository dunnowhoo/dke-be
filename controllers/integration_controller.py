# -*- coding: utf-8 -*-

import json
import datetime
import logging

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class IntegrationController(http.Controller):
    """REST API endpoints for Marketplace & WhatsApp integration management.

    EPIC07 - PBI-10, PBI-11 : Marketplace/Transaksi (auth & status)
    EPIC02 - PBI-7          : WhatsApp Webhook & Config
    EPIC02 - PBI-8          : WhatsApp Sinkronisasi & Disconnect
    """

    # ══════════════════════════════════════════════════════════════
    # MARKETPLACE (EPIC07 - PBI-10, PBI-11)
    # ══════════════════════════════════════════════════════════════

    @http.route('/api/integration/marketplace/auth', type='json', auth='user', methods=['POST'])
    def marketplace_auth(self, **kwargs):
        """POST /api/integration/marketplace/auth — Authenticate with marketplace.

        EPIC07 - PBI-10: Infrastruktur koneksi untuk sinkronisasi data transaksi.
        """
        # TODO: Implement marketplace OAuth authentication
        return {'status': 'ok'}

    @http.route('/api/integration/marketplace/status', type='json', auth='user', methods=['POST'])
    def marketplace_status(self, **kwargs):
        """GET /api/integration/marketplace/status — Check connection status.

        EPIC07 - PBI-10: Cek status koneksi sebelum sync transaksi.
        """
        # TODO: Implement connection status check
        return {'status': 'ok', 'connected': False, 'last_sync': None}

    @http.route('/api/integration/marketplace/send', type='json', auth='user', methods=['POST'])
    def marketplace_send(self, **kwargs):
        """POST /api/integration/marketplace/send — Send message via marketplace.

        EPIC07 - PBI-11: Digunakan saat sinkronisasi status transaksi marketplace.
        """
        # TODO: Implement marketplace message sending
        return {'status': 'ok'}

    # ══════════════════════════════════════════════════════════════
    # WHATSAPP (EPIC02 - PBI-7)
    # ══════════════════════════════════════════════════════════════

    @http.route(
        '/api/integration/whatsapp/webhook',
        type='http',
        auth='none',
        methods=['GET', 'POST'],
        csrf=False,
    )
    def whatsapp_webhook(self, **kwargs):
        """WhatsApp webhook endpoint (public, no auth).

        GET  — Meta verification challenge.
        POST — Incoming messages from WhatsApp Business API.

        EPIC02 - PBI-7: Simpan data chat customer masuk, deduplikasi by message_id,
                        update last_sync_time di tabel konfigurasi.
        """
        if request.httprequest.method == 'GET':
            return self._whatsapp_verify(kwargs)
        return self._whatsapp_receive()

    def _whatsapp_verify(self, params):
        """Handle Meta webhook verification challenge (GET)."""
        hub_mode = params.get('hub.mode') or request.params.get('hub.mode')
        hub_token = params.get('hub.verify_token') or request.params.get('hub.verify_token')
        hub_challenge = params.get('hub.challenge') or request.params.get('hub.challenge')

        account = request.env['whatsapp.account'].sudo().search(
            [('active', '=', True)], limit=1,
        )
        expected_token = account.webhook_verify_token if account else ''

        if hub_mode == 'subscribe' and hub_token == expected_token:
            _logger.info("WhatsApp webhook verified successfully.")
            return request.make_response(
                hub_challenge or '',
                headers=[('Content-Type', 'text/plain')],
            )

        _logger.warning("WhatsApp webhook verification failed. token=%s", hub_token)
        return request.make_response(
            'Forbidden',
            headers=[('Content-Type', 'text/plain')],
            status=403,
        )

    def _whatsapp_receive(self):
        """Process incoming WhatsApp message payload (POST)."""
        try:
            raw_data = request.httprequest.data
            if not raw_data:
                return request.make_response('OK', headers=[('Content-Type', 'text/plain')])

            payload = json.loads(raw_data)

            # Validate it's a WhatsApp Business Account event
            if payload.get('object') != 'whatsapp_business_account':
                return request.make_response('OK', headers=[('Content-Type', 'text/plain')])

            for entry in payload.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    messages = value.get('messages', [])
                    contacts = value.get('contacts', [])

                    for msg in messages:
                        self._process_single_message(msg, contacts)

        except json.JSONDecodeError:
            _logger.error("WhatsApp webhook: invalid JSON payload.")
        except Exception as e:
            _logger.error("WhatsApp webhook error: %s", str(e), exc_info=True)

        # Always return 200 to prevent Meta from retrying
        return request.make_response('OK', headers=[('Content-Type', 'text/plain')])

    def _process_single_message(self, msg, contacts):
        """Parse one message object and persist to DB.

        EPIC02 - PBI-7 business rules:
        - Deduplicate by WhatsApp message_id.
        - Find or create res.partner by phone.
        - Find or create dke.chat.room (per phone, source=whatsapp).
        - Create dke.chat.message.
        - Update dke.whatsapp.config.last_sync_time.
        """
        wa_message_id = msg.get('id', '')
        phone = msg.get('from', '')
        timestamp_raw = msg.get('timestamp', '')
        msg_type = msg.get('type', 'text')

        if not phone or not wa_message_id:
            return

        # ── 1. Deduplicate ──────────────────────────────────────
        already_exists = request.env['dke.chat.message'].sudo().search_count([
            ('external_message_id', '=', wa_message_id)
        ])
        if already_exists:
            _logger.debug("WhatsApp: duplicate message skipped: %s", wa_message_id)
            return

        # ── 2. Extract content ──────────────────────────────────
        content = ''
        if msg_type == 'text':
            content = msg.get('text', {}).get('body', '')
        elif msg_type == 'image':
            content = '[Image]'
        elif msg_type == 'document':
            caption = msg.get('document', {}).get('caption', '')
            content = f'[Document] {caption}'.strip()
        elif msg_type == 'audio':
            content = '[Audio]'
        elif msg_type == 'video':
            content = '[Video]'
        else:
            content = f'[{msg_type}]'

        # Normalise to dke.chat.message.message_type selection values
        if msg_type not in ('text', 'image'):
            stored_type = 'file'
        else:
            stored_type = msg_type

        # ── 3. Resolve customer name from contacts array ────────
        customer_name = phone
        for contact in contacts:
            if contact.get('wa_id') == phone:
                customer_name = contact.get('profile', {}).get('name', phone)
                break

        # ── 4. Find or create res.partner ───────────────────────
        partner = request.env['res.partner'].sudo().search([
            ('phone', '=', phone)
        ], limit=1)
        if not partner:
            partner = request.env['res.partner'].sudo().create({
                'name': customer_name,
                'phone': phone,
            })

        # ── 5. Find or create dke.chat.room ────────────────────
        chat_room = request.env['dke.chat.room'].sudo().search([
            ('external_conversation_id', '=', phone),
            ('source', '=', 'whatsapp'),
        ], limit=1)
        if not chat_room:
            chat_room = request.env['dke.chat.room'].sudo().create({
                'name': f'WA: {customer_name} ({phone})',
                'customer_name': customer_name,
                'customer_id': partner.id,
                'external_conversation_id': phone,
                'source': 'whatsapp',
                'state': 'active',
                'is_assigned': False,
            })
        else:
            # Update name if it was previously just the phone number
            if chat_room.customer_name == phone and customer_name != phone:
                chat_room.sudo().write({'customer_name': customer_name})

        # ── 6. Parse timestamp ──────────────────────────────────
        try:
            msg_time = datetime.datetime.utcfromtimestamp(int(timestamp_raw))
        except (ValueError, TypeError):
            msg_time = fields.Datetime.now()

        # ── 7. Create dke.chat.message ──────────────────────────
        request.env['dke.chat.message'].sudo().create({
            'room_id': chat_room.id,
            'external_message_id': wa_message_id,
            'sender_type': 'customer',
            'content_text': content,
            'message_type': stored_type,
            'is_automated': False,
            'created_at': msg_time,
        })

        # ── 8. Update chat_room last_message_time ───────────────
        chat_room.sudo().write({'last_message_time': msg_time})

        # ── 9. Post to Discuss (mail.thread) for Odoo inbox ────
        try:
            chat_room.sudo().message_post(
                body='[%s] %s' % (customer_name, content),
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        except Exception:
            _logger.warning('Failed to post WA message to Discuss for room %s', chat_room.id, exc_info=True)

        _logger.info(
            "WhatsApp: saved message %s from %s (room=%s)",
            wa_message_id, phone, chat_room.id,
        )

    # ── Config Endpoints (bridged to Odoo whatsapp.account) ──────

    @staticmethod
    def _account_to_dict(account):
        """Serialize whatsapp.account to API-safe dict."""
        return {
            'state': 'connected',
            'name': account.name or '',
            'app_id': account.app_uid or '',
            'account_id': account.account_uid or '',
            'phone_number_id': account.phone_uid or '',
            'phone_number': account.phone_uid or '',
            'webhook_url': account.callback_url or '',
            'webhook_verify_token': account.webhook_verify_token or '',
            'last_sync_time': None,
        }

    _EMPTY_CONFIG = {
        'state': 'disconnected',
        'name': None,
        'app_id': None,
        'account_id': None,
        'phone_number_id': None,
        'phone_number': None,
        'webhook_url': None,
        'webhook_verify_token': None,
        'last_sync_time': None,
    }

    @http.route(
        '/api/integration/whatsapp/status',
        type='http', auth='user', methods=['GET'], csrf=False, cors='*',
    )
    def whatsapp_status(self, **kwargs):
        """GET /api/integration/whatsapp/status — Cek status koneksi.

        EPIC02 - PBI-7: Reads from Odoo whatsapp.account.
        """
        try:
            account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            data = self._account_to_dict(account) if account else self._EMPTY_CONFIG
            return request.make_json_response({'status': 'success', 'data': data})
        except Exception as e:
            _logger.error('whatsapp_status error: %s', e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500,
            )

    @http.route(
        ['/api/integration/whatsapp/auth', '/api/integration/whatsapp/config'],
        type='http', auth='user', methods=['POST', 'PUT'], csrf=False, cors='*',
    )
    def whatsapp_auth(self, **kwargs):
        """POST|PUT /api/integration/whatsapp/auth — Save & validate config.

        Body: { name, app_id, app_secret, account_id, phone_number_id, api_token }
        Creates or updates Odoo whatsapp.account and tests connection.
        """
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}

            app_id = (body.get('app_id') or '').strip()
            app_secret = (body.get('app_secret') or '').strip()
            account_id = (body.get('account_id') or '').strip()
            phone_number_id = (body.get('phone_number_id') or '').strip()
            token = (body.get('api_token') or '').strip()
            name = (body.get('name') or 'DKE WhatsApp').strip()

            # Validation — all five credential fields are required
            missing = []
            if not app_id:
                missing.append('App ID')
            if not app_secret:
                missing.append('App Secret')
            if not account_id:
                missing.append('Account ID')
            if not phone_number_id:
                missing.append('Phone Number ID')
            if not token:
                missing.append('Access Token')

            account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )

            # For updates, only require fields that were actually sent
            if account:
                missing = []  # allow partial update

            if missing:
                return request.make_json_response(
                    {'status': 'error', 'message': '%s wajib diisi.' % ', '.join(missing)},
                    status=400,
                )

            vals = {}
            if name:
                vals['name'] = name
            if app_id:
                vals['app_uid'] = app_id
            if app_secret:
                vals['app_secret'] = app_secret
            if account_id:
                vals['account_uid'] = account_id
            if phone_number_id:
                vals['phone_uid'] = phone_number_id
            if token:
                vals['token'] = token

            if account:
                account.sudo().write(vals)
            else:
                account = request.env['whatsapp.account'].sudo().create(vals)

            # Test connection via Odoo WhatsApp addon
            try:
                account.button_test_connection()
            except (UserError, ValidationError) as ve:
                return request.make_json_response(
                    {'status': 'error', 'message': str(ve)}, status=400,
                )

            return request.make_json_response({
                'status': 'success',
                'message': 'WhatsApp berhasil terhubung!',
                'data': self._account_to_dict(account),
            })
        except json.JSONDecodeError:
            return request.make_json_response(
                {'status': 'error', 'message': 'Invalid JSON body.'}, status=400,
            )
        except Exception as e:
            _logger.error('whatsapp_auth error: %s', e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500,
            )

    @http.route(
        '/api/integration/whatsapp/test',
        type='http', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def whatsapp_test(self, **kwargs):
        """POST /api/integration/whatsapp/test — Test koneksi.

        EPIC02 - PBI-8: Calls Odoo whatsapp.account.button_test_connection.
        """
        try:
            account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            if not account:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Konfigurasi WhatsApp belum ada.'},
                    status=404,
                )
            try:
                account.button_test_connection()
            except (UserError, ValidationError) as ve:
                return request.make_json_response(
                    {'status': 'error', 'message': str(ve)}, status=400,
                )
            return request.make_json_response({
                'status': 'success',
                'message': 'Koneksi berhasil!',
                'data': self._account_to_dict(account),
            })
        except Exception as e:
            _logger.error('whatsapp_test error: %s', e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500,
            )

    @http.route(
        '/api/integration/whatsapp/disconnect',
        type='http', auth='user', methods=['POST'], csrf=False, cors='*',
    )
    def whatsapp_disconnect(self, **kwargs):
        """POST /api/integration/whatsapp/disconnect — Putuskan koneksi.

        EPIC02 - PBI-8: Deactivates the whatsapp.account record.
        """
        try:
            account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            if account:
                account.sudo().write({'active': False})
            return request.make_json_response(
                {'status': 'success', 'message': 'WhatsApp berhasil diputus.'},
            )
        except Exception as e:
            _logger.error('whatsapp_disconnect error: %s', e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500,
            )

    @http.route(
        '/api/integration/whatsapp/sync',
        type='http', auth='user', methods=['GET'], csrf=False, cors='*',
    )
    def whatsapp_sync(self, **kwargs):
        """GET /api/integration/whatsapp/sync — Sinkronisasi template.

        EPIC02 - PBI-8: Calls Odoo whatsapp.account.button_sync_whatsapp_account_templates.
        """
        try:
            account = request.env['whatsapp.account'].sudo().search(
                [('active', '=', True)], limit=1,
            )
            if not account:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Konfigurasi WhatsApp belum ada.'},
                    status=404,
                )
            try:
                account.button_sync_whatsapp_account_templates()
            except (UserError, ValidationError) as ve:
                return request.make_json_response(
                    {'status': 'error', 'message': str(ve)}, status=400,
                )
            return request.make_json_response({
                'status': 'success',
                'message': 'Template berhasil disinkronisasi.',
                'data': {'synced_at': fields.Datetime.to_string(fields.Datetime.now())},
            })
        except Exception as e:
            _logger.error('whatsapp_sync error: %s', e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500,
            )
