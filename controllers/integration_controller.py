# -*- coding: utf-8 -*-

import json
import datetime
import logging

from odoo import http, fields
from odoo.http import request

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

        config = request.env['dke.whatsapp.config'].sudo().get_active_config()
        expected_token = config.webhook_verify_token if config else ''

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

        # ── 9. Update last_sync_time on config ──────────────────
        config = request.env['dke.whatsapp.config'].sudo().get_active_config()
        if config:
            config.sudo().write({'last_sync_time': fields.Datetime.now()})

        _logger.info(
            "WhatsApp: saved message %s from %s (room=%s)",
            wa_message_id, phone, chat_room.id,
        )

    # ── Config Endpoints ────────────────────────────────────────

    @http.route('/api/integration/whatsapp/auth', type='json', auth='user', methods=['POST'])
    def whatsapp_auth(self, **kwargs):
        """POST /api/integration/whatsapp/auth — Save & validate WhatsApp config.

        Body: { api_token, phone_number_id, webhook_url, webhook_verify_token }

        EPIC02 - PBI-7: Validates token against Meta API (401 if expired/invalid).
        Catat last_sync_time jika koneksi berhasil.
        """
        api_token = kwargs.get('api_token', '').strip()
        phone_number_id = kwargs.get('phone_number_id', '').strip()
        webhook_url = kwargs.get('webhook_url', '').strip()
        webhook_verify_token = kwargs.get('webhook_verify_token', '').strip()

        if not api_token or not phone_number_id:
            return {
                'status': 'error',
                'message': 'api_token dan phone_number_id wajib diisi.',
            }

        env = request.env

        # Get or create singleton config
        config = env['dke.whatsapp.config'].sudo().get_active_config()
        vals = {
            'api_token': api_token,
            'phone_number_id': phone_number_id,
            'webhook_url': webhook_url or False,
            'webhook_verify_token': webhook_verify_token or False,
        }
        if config:
            config.sudo().write(vals)
        else:
            config = env['dke.whatsapp.config'].sudo().create(vals)

        # Validate token
        success, message = config.validate_token()
        if not success:
            http_code = 401 if 'expired' in message.lower() or '401' in message else 400
            return {
                'status': 'error',
                'message': message,
                'http_code': http_code,
            }

        config.sudo().write({'last_sync_time': fields.Datetime.now()})

        return {
            'status': 'success',
            'message': message,
            'data': {
                'state': config.state,
                'phone_number': config.phone_number,
                'last_sync_time': fields.Datetime.to_string(config.last_sync_time),
            },
        }

    @http.route('/api/integration/whatsapp/status', type='json', auth='user', methods=['POST'])
    def whatsapp_status(self, **kwargs):
        """GET /api/integration/whatsapp/status — Cek status koneksi WhatsApp.

        EPIC02 - PBI-7: Endpoint untuk card status di /settings/integrations.
        """
        config = request.env['dke.whatsapp.config'].sudo().get_active_config()

        if not config:
            return {
                'status': 'success',
                'data': {
                    'state': 'disconnected',
                    'phone_number': None,
                    'phone_number_id': None,
                    'webhook_url': None,
                    'last_sync_time': None,
                },
            }

        return {
            'status': 'success',
            'data': {
                'state': config.state,
                'phone_number': config.phone_number,
                'phone_number_id': config.phone_number_id,
                'webhook_url': config.webhook_url,
                'last_sync_time': fields.Datetime.to_string(config.last_sync_time) if config.last_sync_time else None,
            },
        }

    @http.route('/api/integration/whatsapp/disconnect', type='json', auth='user', methods=['POST'])
    def whatsapp_disconnect(self, **kwargs):
        """POST /api/integration/whatsapp/disconnect — Putuskan koneksi WhatsApp.

        EPIC02 - PBI-8: Disconnect button di settings/integrations.
        """
        config = request.env['dke.whatsapp.config'].sudo().get_active_config()
        if config:
            config.disconnect()

        return {'status': 'success', 'message': 'WhatsApp berhasil diputus.'}

    @http.route('/api/integration/whatsapp/test', type='json', auth='user', methods=['POST'])
    def whatsapp_test(self, **kwargs):
        """POST /api/integration/whatsapp/test — Test koneksi dengan token saat ini.

        EPIC02 - PBI-8: Tombol 'Test Connection' di settings/integrations.
        """
        config = request.env['dke.whatsapp.config'].sudo().get_active_config()
        if not config:
            return {'status': 'error', 'message': 'Konfigurasi WhatsApp belum ada.'}

        success, message = config.validate_token()
        return {
            'status': 'success' if success else 'error',
            'message': message,
            'data': {
                'state': config.state,
                'phone_number': config.phone_number,
            },
        }
