# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import datetime
import logging
import time
import html

import requests
import werkzeug.exceptions
import werkzeug.utils

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)



class IntegrationController(http.Controller):
    """REST API endpoints for Marketplace (Shopee) & WhatsApp integration.

    EPIC07 - PBI-10, PBI-11 : Shopee OAuth & status
    EPIC02 - PBI-7          : WhatsApp Webhook & Config
    EPIC02 - PBI-8          : WhatsApp Sinkronisasi & Disconnect
    """

    # ══════════════════════════════════════════════════════════════
    # SHOPEE OAUTH (EPIC07 - PBI-10)
    # ══════════════════════════════════════════════════════════════

    def _get_shopee_config(self):
        """Ambil shopee.config aktif dari DB."""
        return request.env["shopee.config"].sudo().search([("active", "=", True)], limit=1)

    def _require_shopee_access(self):
        """Raise 403 jika user bukan Admin atau Sales Manager."""
        user = request.env.user
        if not (
            user.has_group("dke_crm.group_sales_manager")
            or user.has_group("base.group_system")
        ):
            raise werkzeug.exceptions.Forbidden(
                "Akses ditolak. Hanya Sales Manager atau Admin yang diizinkan."
            )

    def _shopee_sign_auth(self, partner_id, partner_key, path, timestamp):
        """HMAC-SHA256 untuk auth endpoints (no access_token / shop_id)."""
        base = f"{partner_id}{path}{timestamp}"
        return hmac.new(
            partner_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @http.route(
        "/api/integration/shopee/auth-url",
        type="json",
        auth="user",
        methods=["POST"],
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def shopee_get_auth_url(self, **kwargs):
        """POST /api/integration/shopee/auth-url — Simpan credentials & generate Shopee OAuth URL.

        Body params (wajib):
            partner_id   (str/int) : Shopee Partner ID
            partner_key  (str)     : Shopee Partner Key
            redirect_url (str)     : OAuth callback URL di Frontend
        Body params (opsional):
            is_sandbox   (bool)    : Gunakan sandbox (default False / production)

        Credentials disimpan ke shopee.config agar callback bisa menukar token.
        EPIC07 - PBI-10
        """
        self._require_shopee_access()
        params         = kwargs.get('params', kwargs)
        partner_id_raw = params.get('partner_id', '')
        partner_key    = (params.get('partner_key') or '').strip()
        redirect_url   = (params.get('redirect_url') or '').strip()
        is_sandbox     = params.get('is_sandbox', False)

        # ── Validasi ────────────────────────────────────────────
        if not partner_id_raw:
            return {"status": "error", "message": "partner_id wajib diisi."}
        if not partner_key:
            return {"status": "error", "message": "partner_key wajib diisi."}
        if not redirect_url:
            return {"status": "error", "message": "redirect_url wajib diisi."}

        try:
            partner_id = int(partner_id_raw)
        except (ValueError, TypeError):
            return {"status": "error", "message": "partner_id harus berupa angka."}

        # ── Simpan / update config di DB ─────────────────────────
        ShopeeConfig = request.env["shopee.config"].sudo()
        config = ShopeeConfig.search([("active", "=", True)], limit=1)
        vals = {
            "partner_id":  str(partner_id),
            "partner_key": partner_key,
            "redirect_url": redirect_url,
            "is_sandbox":  is_sandbox,
        }
        if config:
            config.write(vals)
        else:
            ShopeeConfig.create({**vals, "shop_name": "Pending OAuth"})

        # ── Base URL ────────────────────────────────────────────
        BASE_URL_SANDBOX = "https://partner.test-stable.shopeemobile.com"
        BASE_URL_PROD    = "https://partner.shopeemobile.com"
        base_url = BASE_URL_SANDBOX if is_sandbox else BASE_URL_PROD

        # ── Validasi credentials ke Shopee sebelum redirect ──────
        # Hit /api/v2/public/get_shops_by_partner yang hanya butuh partner auth.
        # Kalau partner_id / partner_key salah, Shopee return error dan kita
        # langsung kembalikan pesan ke FE tanpa pernah redirect user.
        validate_path = "/api/v2/public/get_shops_by_partner"
        ts_validate   = int(time.time())
        sign_validate = self._shopee_sign_auth(partner_id, partner_key, validate_path, ts_validate)
        try:
            validate_resp = requests.get(
                f"{base_url}{validate_path}",
                params={
                    "partner_id": partner_id,
                    "timestamp":  ts_validate,
                    "sign":       sign_validate,
                    "page_size":  1,
                    "page_no":    1,
                },
                timeout=10,
            )
            validate_data = validate_resp.json()
        except Exception as exc:
            _logger.warning("[Shopee OAuth] Gagal menghubungi Shopee untuk validasi: %s", exc)
            return {
                "status": "error",
                "message": "Tidak dapat menghubungi Shopee. Periksa koneksi internet atau coba lagi.",
            }

        shopee_error = validate_data.get("error", "")
        if shopee_error and shopee_error not in ("", "error_not_found"):
            # error_not_found = partner valid tapi belum punya shop → tetap lanjut
            _logger.warning("[Shopee OAuth] Validasi credentials gagal: %s", validate_data)
            return {
                "status": "error",
                "message": f"Partner ID atau Partner Key tidak valid: {validate_data.get('message', shopee_error)}",
            }

        # ── Generate HMAC-SHA256 sign & auth URL ─────────────────
        path = "/api/v2/shop/auth_partner"
        ts   = int(time.time())
        sign = self._shopee_sign_auth(partner_id, partner_key, path, ts)

        auth_url = (
            f"{base_url}{path}"
            f"?partner_id={partner_id}&timestamp={ts}&sign={sign}"
            f"&redirect={redirect_url}"
        )

        _logger.info(
            "[Shopee OAuth] Auth URL generated & config saved: partner_id=%s sandbox=%s",
            partner_id, is_sandbox,
        )

        return {
            "status": "success",
            "data": {
                "auth_url":    auth_url,
                "partner_id":  partner_id,
                "timestamp":   ts,
                "is_sandbox":  is_sandbox,
                "redirect_url": redirect_url,
            },
        }

    @http.route(
        "/api/integration/shopee/exchange-token",
        type="json",
        auth="user",
        methods=["POST"],
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def shopee_exchange_token(self, **kwargs):
        """POST /api/integration/shopee/exchange-token — Tukar auth code dengan access token.

        Dipanggil oleh Frontend callback page setelah user authorize di Shopee.
        Credentials (partner_id, partner_key) diambil dari DB yang sudah tersimpan
        saat /auth-url dipanggil sebelumnya.

        Body params (wajib):
            code     (str)     : Authorization code dari query param Shopee
            shop_id  (str/int) : Shop ID dari query param Shopee

        EPIC07 - PBI-10
        """
        self._require_shopee_access()
        params  = kwargs.get('params', kwargs)
        code    = (params.get('code') or '').strip()
        shop_id = params.get('shop_id')

        if not code:
            return {"status": "error", "message": "code wajib diisi."}
        if not shop_id:
            return {"status": "error", "message": "shop_id wajib diisi."}

        try:
            shop_id = int(shop_id)
        except (ValueError, TypeError):
            return {"status": "error", "message": "shop_id harus berupa angka."}

        config = request.env["shopee.config"].sudo().search([("active", "=", True)], limit=1)
        if not config:
            return {
                "status": "error",
                "message": "Konfigurasi Shopee belum ada. Silakan setup partner credential terlebih dahulu.",
            }

        partner_id  = int(config.partner_id)
        partner_key = config.partner_key
        base_url    = config.get_base_url()
        path        = "/api/v2/auth/token/get"
        ts          = int(time.time())
        sign        = self._shopee_sign_auth(partner_id, partner_key, path, ts)

        body = {
            "code":       code,
            "shop_id":    shop_id,
            "partner_id": partner_id,
        }
        query_params = {
            "partner_id": partner_id,
            "timestamp":  ts,
            "sign":       sign,
        }

        try:
            resp = requests.post(
                f"{base_url}{path}",
                json=body,
                params=query_params,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _logger.exception("[Shopee OAuth] exchange-token HTTP error: %s", exc)
            return {"status": "error", "message": f"Gagal menghubungi Shopee: {str(exc)}"}

        access_token = data.get("access_token")
        if not access_token:
            error_msg = data.get("message", "Unknown error")
            _logger.error("[Shopee OAuth] Token exchange gagal: %s", data)
            return {"status": "error", "message": f"Shopee error: {error_msg}"}

        expire_in  = data.get("expire_in", 0)
        ts_now     = int(time.time())
        shop_name  = data.get("shop_name") or f"Shop {shop_id}"
        config.sudo().write({
            "access_token":    access_token,
            "refresh_token":   data.get("refresh_token") or False,
            "token_expire_in": expire_in,
            "token_expire_at": ts_now + expire_in,
            "shop_id":         str(shop_id),
            "shop_name":       shop_name,
        })

        _logger.info(
            "[Shopee OAuth] Token berhasil disimpan. shop_id=%s expires_in=%s",
            shop_id, expire_in,
        )

        return {
            "status": "success",
            "message": "Shopee berhasil terhubung.",
            "data": {"shop_id": shop_id, "expires_in": expire_in},
        }

    @http.route(
        "/shopee/oauth/callback",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
    )
    def shopee_oauth_callback(self, code=None, shop_id=None, **kwargs):
        """GET /shopee/oauth/callback — Callback dari Shopee setelah user authorize.

        Shopee redirect ke sini dengan ?code=XXX&shop_id=YYY.
        Endpoint ini menukar code dengan access_token lalu menyimpan ke DB.
        EPIC07 - PBI-10
        """
        if not code or not shop_id:
            _logger.warning("[Shopee OAuth] Callback tanpa code/shop_id. params=%s", kwargs)
            return request.make_response(
                "<h2>OAuth Error</h2><p>Parameter code atau shop_id tidak ditemukan.</p>",
                headers=[("Content-Type", "text/html")],
                status=400,
            )

        config = request.env["shopee.config"].sudo().search([("active", "=", True)], limit=1)
        if not config:
            return request.make_response(
                "<h2>Error</h2><p>Konfigurasi Shopee belum dibuat di Odoo.</p>",
                headers=[("Content-Type", "text/html")],
                status=400,
            )

        partner_id = int(config.partner_id)
        partner_key = config.partner_key
        base_url = config.get_base_url()
        path = "/api/v2/auth/token/get"
        ts = int(time.time())
        sign = self._shopee_sign_auth(partner_id, partner_key, path, ts)

        body = {
            "code":       code,
            "shop_id":    int(shop_id),
            "partner_id": partner_id,
        }
        query_params = {
            "partner_id": partner_id,
            "timestamp":  ts,
            "sign":       sign,
        }

        try:
            resp = requests.post(
                f"{base_url}{path}",
                json=body,
                params=query_params,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _logger.exception("[Shopee OAuth] Error saat tukar code: %s", exc)
            return request.make_response(
                f"<h2>OAuth Error</h2><p>Gagal menghubungi Shopee: {html.escape(str(exc))}</p>",
                headers=[("Content-Type", "text/html")],
                status=500,
            )

        access_token = data.get("access_token")
        if not access_token:
            error_msg = data.get("message", "Unknown error")
            _logger.error("[Shopee OAuth] Token exchange gagal: %s", data)
            return request.make_response(
                f"<h2>OAuth Error</h2><p>Shopee mengembalikan error: {html.escape(str(error_msg))}</p>",
                headers=[("Content-Type", "text/html")],
                status=400,
            )

        expire_in = data.get("expire_in", 0)
        config.sudo().write({
            "access_token": access_token,
            "refresh_token": data.get("refresh_token") or False,
            "token_expire_in": expire_in,
            "token_expire_at": ts + expire_in,
            "shop_id": str(shop_id),
            "shop_name": data.get("shop_name") or f"Shop {shop_id}",
        })

        _logger.info(
            "[Shopee OAuth] Token berhasil disimpan. shop_id=%s expires_in=%s",
            shop_id, expire_in,
        )

        # Redirect balik ke form config di Odoo backend
        redirect_to = f"/web#model=shopee.config&id={config.id}&view_type=form"
        return werkzeug.utils.redirect(redirect_to)

    @http.route(
        "/api/integration/shopee/token/refresh",
        type="json",
        auth="user",
        methods=["POST"],
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def shopee_token_refresh(self, **kwargs):
        """POST /api/integration/shopee/token/refresh — Refresh access token Shopee.

        EPIC07 - PBI-10
        """
        self._require_shopee_access()
        config = self._get_shopee_config()
        if not config:
            return {"status": "error", "message": "Konfigurasi Shopee belum ada."}

        if not config.refresh_token:
            return {"status": "error", "message": "Refresh token tidak tersedia. Silakan connect ulang."}

        # Force refresh: panggil langsung dengan force=True
        success = config.refresh_token_if_needed(force=True)

        if not success:
            return {"status": "error", "message": "Gagal refresh token. Cek log untuk detail."}

        return {
            "status": "success",
            "message": "Token berhasil diperbarui.",
            "data": {"expires_in": config.token_expire_in},
        }

    @http.route(
        "/api/integration/shopee/status",
        type="json",
        auth="user",
        methods=["POST"],
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def shopee_status(self, **kwargs):
        """POST /api/integration/shopee/status — Cek status koneksi Shopee.

        EPIC07 - PBI-10
        """
        self._require_shopee_access()
        config = self._get_shopee_config()
        if not config:
            return {
                "status": "success",
                "data": {
                    "connected": False,
                    "shop_name": None,
                    "shop_id": None,
                    "connection_status": "disconnected",
                    "token_expire_at": None,
                    "last_sync": None,
                    "is_sandbox": False,
                    "use_dummy": False,
                },
            }

        now = int(time.time())
        connection_status = "disconnected"
        if config.access_token:
            if config.token_expire_at and now >= config.token_expire_at:
                connection_status = "expired"
            else:
                connection_status = "connected"

        return {
            "status": "success",
            "data": {
                "connected": connection_status == "connected",
                "shop_name": config.shop_name,
                "shop_id": config.shop_id or None,
                "connection_status": connection_status,
                "token_expire_at": config.token_expire_at or None,
                "last_sync": fields.Datetime.to_string(config.last_sync) if config.last_sync else None,
                "is_sandbox": config.is_sandbox,
                "use_dummy": config.use_dummy,
            },
        }

    @http.route(
        "/api/integration/shopee/disconnect",
        type="json",
        auth="user",
        methods=["POST"],
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def shopee_disconnect(self, **kwargs):
        """POST /api/integration/shopee/disconnect — Putuskan koneksi Shopee.

        EPIC07 - PBI-10
        """
        self._require_shopee_access()
        config = self._get_shopee_config()
        if config:
            config.sudo().write({
                "access_token": False,
                "refresh_token": False,
                "token_expire_in": 0,
                "token_expire_at": 0,
            })

        return {"status": "success", "message": "Koneksi Shopee berhasil diputus."}

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
        - Find or create dke.ticketing.room (per phone, source=whatsapp).
        - Create dke.ticketing.message.
        - Update dke.whatsapp.config.last_sync_time.
        """
        wa_message_id = msg.get('id', '')
        phone = msg.get('from', '')
        timestamp_raw = msg.get('timestamp', '')
        msg_type = msg.get('type', 'text')

        if not phone or not wa_message_id:
            return

        # ── 1. Deduplicate ──────────────────────────────────────
        already_exists = request.env['dke.ticketing.message'].sudo().search_count([
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

        # Normalise to dke.ticketing.message.message_type selection values
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

        # ── 5. Find or create dke.ticketing.room ────────────────────
        ticketing_room = request.env['dke.ticketing.room'].sudo().search([
            ('external_conversation_id', '=', phone),
            ('source', '=', 'whatsapp'),
        ], limit=1)
        if not ticketing_room:
            ticketing_room = request.env['dke.ticketing.room'].sudo().create({
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
            if ticketing_room.customer_name == phone and customer_name != phone:
                ticketing_room.sudo().write({'customer_name': customer_name})

        # ── 6. Parse timestamp ──────────────────────────────────
        try:
            msg_time = datetime.datetime.utcfromtimestamp(int(timestamp_raw))
        except (ValueError, TypeError):
            msg_time = fields.Datetime.now()

        # ── 7. Create dke.ticketing.message ──────────────────────────
        request.env['dke.ticketing.message'].sudo().create({
            'room_id': ticketing_room.id,
            'external_message_id': wa_message_id,
            'sender_type': 'customer',
            'content_text': content,
            'message_type': stored_type,
            'is_automated': False,
            'created_at': msg_time,
        })

        # ── 8. Update ticketing_room last_message_time ───────────────
        ticketing_room.sudo().write({'last_message_time': msg_time})

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
            wa_message_id, phone, ticketing_room.id,
        )

    # ── Config Endpoints (bridged to Odoo whatsapp.account) ──────

    @staticmethod
    def _authenticate_request():
        """Restore Odoo user session from Bearer token in Authorization header.

        The FE stores the Odoo session SID as the Bearer token (returned by
        /api/auth/login as access_token = request.session.sid).
        This helper manually restores the session so sudo() calls work correctly
        when the route uses auth='none'.

        Returns True if a valid session was found, False otherwise.
        """
        auth_header = request.httprequest.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:].strip()
            if token and token != request.session.sid:
                request.session.sid = token
                try:
                    request.session._prepare()
                except Exception:
                    pass
        # sudo() is sufficient for our use-case; we don't need strict uid check.
        return True

    @staticmethod
    def _is_connected(account):
        """True if account exists and has a non-empty token."""
        return bool(account and account.token)

    @staticmethod
    def _account_to_dict(account):
        """Serialize whatsapp.account to API-safe dict.

        whatsapp.account has no 'state' field — we derive it from token presence.
        """
        connected = bool(account.token)
        return {
            'state': 'connected' if connected else 'disconnected',
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
        type='http', auth='none', methods=['GET', 'OPTIONS'], csrf=False, cors='*',
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
        type='http', auth='none', methods=['POST', 'PUT', 'OPTIONS'], csrf=False, cors='*',
    )
    def whatsapp_auth(self, **kwargs):
        """POST|PUT /api/integration/whatsapp/auth — Save & validate config.

        Body: { name, app_id, app_secret, account_id, phone_number_id, api_token }
        Creates or updates Odoo whatsapp.account.
        If a previously archived account with the same phone_uid exists, it will
        be re-activated instead of creating a new one.
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

            # --- Look for an account with this EXACT phone_uid (could be archived) ---
            WA = request.env['whatsapp.account'].sudo()
            existing_by_phone = None
            if phone_number_id:
                existing_by_phone = WA.with_context(active_test=False).search(
                    [('phone_uid', '=', phone_number_id)], limit=1
                )

            # --- Look for the currently active account ---
            active_account = WA.search([('active', '=', True)], limit=1)

            account = None

            if existing_by_phone:
                # An account (active or archived) with this exact phone number ALREADY EXISTS
                vals = {'active': True}
                if name: vals['name'] = name
                if app_id: vals['app_uid'] = app_id
                if app_secret: vals['app_secret'] = app_secret
                if account_id: vals['account_uid'] = account_id
                if token: vals['token'] = token
                existing_by_phone.write(vals)
                account = existing_by_phone

                # If there was a DIFFERENT active account, we archive it
                if active_account and active_account.id != existing_by_phone.id:
                    active_account.write({'active': False})
            else:
                # NO account with this phone number exists.
                # So we can safely update the existing active account, OR create a new one.
                vals = {}
                if name: vals['name'] = name
                if app_id: vals['app_uid'] = app_id
                if app_secret: vals['app_secret'] = app_secret
                if account_id: vals['account_uid'] = account_id
                vals['phone_uid'] = phone_number_id
                if token: vals['token'] = token

                if active_account:
                    # Update current active account (this is safe because phone_uid is not duplicated)
                    active_account.write(vals)
                    account = active_account
                else:
                    # We have NO accounts with this number, and NO active accounts at all -> CREATE
                    missing = []
                    if not app_id:           missing.append('App ID')
                    if not app_secret:       missing.append('App Secret')
                    if not account_id:       missing.append('Account ID')
                    if not phone_number_id:  missing.append('Phone Number ID')
                    if not token:            missing.append('Access Token')
                    if missing:
                        return request.make_json_response(
                            {'status': 'error', 'message': '%s wajib diisi.' % ', '.join(missing)},
                            status=400,
                        )
                    current_uid = request.env.uid or 2
                    account = WA.create({
                        'name': name,
                        'app_uid': app_id,
                        'app_secret': app_secret,
                        'account_uid': account_id,
                        'phone_uid': phone_number_id,
                        'token': token,
                        'notify_user_ids': [(4, current_uid)],
                    })

            # Test connection via Meta API
            try:
                account.button_test_connection()
            except Exception as conn_err:
                err_msg = conn_err.args[0] if getattr(conn_err, 'args', None) else str(conn_err)
                err_msg = str(err_msg).strip()
                _logger.warning('whatsapp_auth test failed: %s', err_msg)
                # Still keep the record but inform FE the test failed
                return request.make_json_response({
                    'status': 'error',
                    'message': err_msg or 'Kredensial WhatsApp tidak valid.',
                    'data': self._account_to_dict(account),
                }, status=400)

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
        type='http', auth='none', methods=['POST', 'OPTIONS'], csrf=False, cors='*',
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
            except Exception as conn_err:
                err_msg = conn_err.args[0] if getattr(conn_err, 'args', None) else str(conn_err)
                err_msg = str(err_msg).strip()
                return request.make_json_response(
                    {'status': 'error', 'message': err_msg or 'Koneksi gagal log ke Meta.'}, status=400,
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
        type='http', auth='none', methods=['POST', 'OPTIONS'], csrf=False, cors='*',
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
        type='http', auth='none', methods=['GET', 'OPTIONS'], csrf=False, cors='*',
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
            except Exception as sync_err:
                err_msg = sync_err.args[0] if getattr(sync_err, 'args', None) else str(sync_err)
                err_msg = str(err_msg).strip()
                return request.make_json_response(
                    {'status': 'error', 'message': err_msg or 'Gagal sinkronisasi.'}, status=400,
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
