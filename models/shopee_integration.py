# -*- coding: utf-8 -*-

# ──────────────────────────────────────────────────────────────────────────────
# Shopee Open API Integration Model
#
# Endpoint Shopee yang akan digunakan (saat akun live sudah siap):
#   - GET /api/v2/order/get_order_list
#   - GET /api/v2/order/get_order_detail
#   - GET /api/v2/payment/get_escrow_detail  (hanya order berstatus COMPLETED)
#
# OAuth Flow:
#   1. User klik "Connect Shopee" → action_connect_shopee() → redirect ke auth page Shopee
#   2. User login & authorize di Shopee → Shopee redirect ke /shopee/oauth/callback?code=&shop_id=
#   3. Callback endpoint tukar code dengan access_token → simpan ke DB
#   4. Siap memanggil data API menggunakan access_token
# ──────────────────────────────────────────────────────────────────────────────

import hashlib
import hmac
import logging
import time

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Model: shopee.config  –  Menyimpan kredensial & pengaturan integrasi Shopee
# ══════════════════════════════════════════════════════════════════════════════
class ShopeeConfig(models.Model):
    _name = "shopee.config"
    _description = "Shopee Integration Configuration"
    _rec_name = "shop_name"

    shop_name = fields.Char(string="Shop Name", required=True)
    shop_id = fields.Char(string="Shop ID")

    # ── Credentials (diisi saat akun Shopee sudah aktif) ──────────────────────
    partner_id = fields.Char(
        string="Partner ID",
        default=lambda self: __import__('os').environ.get("SHOPEE_LIVE_ID", ""),
    )
    partner_key = fields.Char(
        string="Partner Key",
        default=lambda self: __import__('os').environ.get("SHOPEE_PARTNER_KEY", ""),
    )
    access_token = fields.Char(string="Access Token")
    refresh_token = fields.Char(string="Refresh Token")
    token_expire_in = fields.Integer(string="Token Expire In (seconds)")
    token_expire_at = fields.Integer(string="Token Expire At (Unix)")

    # ── Redirect URL untuk OAuth callback ────────────────────────────────────
    redirect_url = fields.Char(
        string="OAuth Redirect URL",
        help="URL callback yang didaftarkan di Shopee Partner Portal. "
             "Harus sama persis dengan yang ada di portal.",
    )

    # ── Status koneksi (computed) ─────────────────────────────────────────────
    connection_status = fields.Selection(
        selection=[
            ("connected", "Connected"),
            ("expired", "Token Expired"),
            ("disconnected", "Disconnected"),
        ],
        string="Connection Status",
        compute="_compute_connection_status",
        store=False,
    )

    # ── Pengaturan ────────────────────────────────────────────────────────────
    is_sandbox = fields.Boolean(
        string="Use Sandbox Mode",
        default=False,
        help="Jika True, mengarah ke endpoint sandbox Shopee (partner.test-stable.shopeemobile.com).",
    )
    use_dummy = fields.Boolean(
        string="Use Dummy Data",
        default=False,
        help="Jika True, semua data diambil dari dummy data bawaan modul (untuk testing FE).",
    )
    active = fields.Boolean(default=True)
    last_sync = fields.Datetime(string="Last Sync")

    @api.depends("access_token", "token_expire_at")
    def _compute_connection_status(self):
        now = int(time.time())
        for rec in self:
            if not rec.access_token:
                rec.connection_status = "disconnected"
            elif rec.token_expire_at and now >= rec.token_expire_at:
                rec.connection_status = "expired"
            else:
                rec.connection_status = "connected"

    # ── Base URL helper ────────────────────────────────────────────────────────

    BASE_URL_SANDBOX = "https://partner.test-stable.shopeemobile.com"
    BASE_URL_PROD = "https://partner.shopeemobile.com"

    def get_base_url(self):
        self.ensure_one()
        return self.BASE_URL_SANDBOX if self.is_sandbox else self.BASE_URL_PROD

    # ── Signature helper ──────────────────────────────────────────────────────

    def _sign_auth(self, path, timestamp):
        """Signature untuk auth endpoints (token get/refresh): no access_token/shop_id."""
        self.ensure_one()
        base_string = f"{self.partner_id}{path}{timestamp}"
        return hmac.new(
            self.partner_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── OAuth Actions (tombol di form view) ───────────────────────────────────

    def action_connect_shopee(self):
        """
        Tombol 'Connect Shopee' di form view.

        Membuat URL authorization Shopee dan redirect user ke sana.
        Setelah user authorize, Shopee akan redirect ke /shopee/oauth/callback?code=&shop_id=
        """
        self.ensure_one()

        if not self.partner_id or not self.partner_key:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Konfigurasi Tidak Lengkap",
                    "message": "Partner ID dan Partner Key wajib diisi sebelum connect.",
                    "type": "warning",
                    "sticky": False,
                },
            }

        partner_id = int(self.partner_id)
        base_url = self.get_base_url()
        redirect_url = self.redirect_url or ""
        if not redirect_url:
            return {"error": "redirect_url wajib diisi sebelum melakukan koneksi Shopee"}
        path = "/api/v2/shop/auth_partner"
        ts = int(time.time())
        sign = self._sign_auth(path, ts)

        auth_url = (
            f"{base_url}{path}"
            f"?partner_id={partner_id}&timestamp={ts}&sign={sign}"
            f"&redirect={redirect_url}"
        )

        _logger.info("[Shopee OAuth] Redirecting to Shopee auth: %s", auth_url)

        return {
            "type": "ir.actions.act_url",
            "url": auth_url,
            "target": "new",
        }

    def action_disconnect_shopee(self):
        """Tombol 'Disconnect' – hapus semua token dari DB."""
        self.ensure_one()
        self.write({
            "access_token": False,
            "refresh_token": False,
            "token_expire_in": 0,
            "token_expire_at": 0,
            "shop_id": 0,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Shopee Disconnected",
                "message": "Koneksi Shopee berhasil diputus.",
                "type": "success",
                "sticky": False,
            },
        }

    # ── Token refresh ─────────────────────────────────────────────────────────

    REFRESH_THRESHOLD_SECONDS = 600  # refresh jika sisa < 10 menit

    def refresh_token_if_needed(self, force: bool = False):
        """Refresh access_token otomatis jika hampir/sudah expired.

        Bisa dipanggil dari mana saja (service, controller, cron).
        Return True jika berhasil refresh, False jika tidak perlu atau gagal.
        Set force=True untuk memaksa refresh tanpa cek threshold.
        """
        import requests as _req

        self.ensure_one()

        if not self.refresh_token:
            return False
        if not self.token_expire_at:
            return False

        now = int(time.time())
        remaining = self.token_expire_at - now

        if remaining > self.REFRESH_THRESHOLD_SECONDS and not force:
            return False  # masih cukup waktu

        _logger.info(
            "[ShopeeConfig] Token hampir expired (sisa %d detik). Auto-refreshing...",
            remaining,
        )

        path = "/api/v2/auth/access_token/get"
        ts = int(time.time())
        sign = self._sign_auth(path, ts)

        query_params = {
            "partner_id": int(self.partner_id),
            "timestamp": ts,
            "sign": sign,
        }
        body = {
            "shop_id": self.shop_id,
            "refresh_token": self.refresh_token,
            "partner_id": int(self.partner_id),
        }

        try:
            resp = _req.post(
                f"{self.get_base_url()}{path}",
                params=query_params,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("error"):
                _logger.error(
                    "[ShopeeConfig] Auto-refresh gagal: [%s] %s",
                    data.get("error"), data.get("message"),
                )
                return False

            new_access = data.get("access_token")
            if not new_access:
                _logger.error("[ShopeeConfig] Auto-refresh: tidak ada access_token di response")
                return False

            expire_in = data.get("expire_in", 14400)
            self.sudo().write({
                "access_token": new_access,
                "refresh_token": data.get("refresh_token", self.refresh_token),
                "token_expire_in": expire_in,
                "token_expire_at": int(time.time()) + expire_in,
            })
            _logger.info(
                "[ShopeeConfig] Auto-refresh berhasil. Token baru berlaku %d detik.",
                expire_in,
            )
            return True

        except Exception as exc:
            _logger.error("[ShopeeConfig] Auto-refresh error: %s", exc)
            return False

# ══════════════════════════════════════════════════════════════════════════════
# Model: shopee.order  –  Cache order dari Shopee
# ══════════════════════════════════════════════════════════════════════════════
class ShopeeOrder(models.Model):
    _name = "shopee.order"
    _description = "Shopee Order"
    _order = "create_time desc"
    _rec_name = "order_sn"

    # ── Identifikasi ──────────────────────────────────────────────────────────
    order_sn = fields.Char(string="Order SN", required=True, index=True)
    config_id = fields.Many2one("shopee.config", string="Shopee Config", ondelete="set null")

    # ── Status ────────────────────────────────────────────────────────────────
    order_status = fields.Selection(
        selection=[
            ("UNPAID", "Unpaid"),
            ("READY_TO_SHIP", "Ready to Ship"),
            ("PROCESSED", "Processed"),
            ("SHIPPED", "Shipped"),
            ("COMPLETED", "Completed"),
            ("IN_CANCEL", "In Cancel"),
            ("CANCELLED", "Cancelled"),
            ("INVOICE_PENDING", "Invoice Pending"),
        ],
        string="Order Status",
        index=True,
    )

    # ── Buyer ─────────────────────────────────────────────────────────────────
    buyer_username = fields.Char(string="Buyer Username")
    buyer_user_id = fields.Char(string="Buyer User ID")

    # ── Nilai Transaksi ───────────────────────────────────────────────────────
    total_amount = fields.Float(string="Total Amount")
    currency = fields.Char(string="Currency", default="IDR")
    estimated_shipping_fee = fields.Float(string="Est. Shipping Fee")
    actual_shipping_cost = fields.Float(string="Actual Shipping Cost")

    # ── Timestamp ─────────────────────────────────────────────────────────────
    create_time = fields.Integer(string="Create Time (Unix)")
    update_time = fields.Integer(string="Update Time (Unix)")
    pay_time = fields.Integer(string="Pay Time (Unix)")

    # ── Pengiriman ────────────────────────────────────────────────────────────
    shipping_carrier = fields.Char(string="Shipping Carrier")
    tracking_number = fields.Char(string="Tracking Number")

    # ── Penerima ──────────────────────────────────────────────────────────────
    recipient_name = fields.Char(string="Recipient Name")
    recipient_phone = fields.Char(string="Recipient Phone")
    recipient_full_address = fields.Text(string="Full Address")
    recipient_town = fields.Char(string="Town/Kelurahan")
    recipient_district = fields.Char(string="District/Kecamatan")
    recipient_city = fields.Char(string="City")
    recipient_state = fields.Char(string="Province")
    recipient_zipcode = fields.Char(string="ZIP Code")

    # ── Misc ─────────────────────────────────────────────────────────────────
    note = fields.Text(string="Buyer Note")
    dropshipper = fields.Char(string="Dropshipper Name")

    # ── Relasi ───────────────────────────────────────────────────────────────
    order_item_ids = fields.One2many(
        "shopee.order.item", "order_id", string="Order Items"
    )
    escrow_id = fields.One2many(
        "shopee.order.escrow", "order_id", string="Escrow Detail"
    )

    _sql_constraints = [
        ("order_sn_unique", "UNIQUE(order_sn)", "Order SN harus unik!"),
    ]

    @api.depends("order_item_ids.subtotal")
    def _compute_items_total(self):
        for rec in self:
            rec.items_subtotal = sum(rec.order_item_ids.mapped("subtotal"))

    items_subtotal = fields.Float(
        string="Items Subtotal",
        compute="_compute_items_total",
        store=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Model: shopee.order.item  –  Baris item per order
# ══════════════════════════════════════════════════════════════════════════════
class ShopeeOrderItem(models.Model):
    _name = "shopee.order.item"
    _description = "Shopee Order Item"
    _rec_name = "item_name"

    order_id = fields.Many2one("shopee.order", string="Order", ondelete="cascade", required=True)

    item_id = fields.Char(string="Item ID")
    item_name = fields.Char(string="Item Name")
    item_sku = fields.Char(string="Item SKU")

    model_id = fields.Char(string="Model ID")
    model_name = fields.Char(string="Model / Variant")
    model_sku = fields.Char(string="Model SKU")

    quantity_purchased = fields.Integer(string="Qty", default=1)
    original_price = fields.Float(string="Original Price")
    discounted_price = fields.Float(string="Discounted Price")
    image_url = fields.Char(string="Image URL")

    subtotal = fields.Float(
        string="Subtotal",
        compute="_compute_subtotal",
        store=True,
    )

    @api.depends("quantity_purchased", "discounted_price")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.quantity_purchased * rec.discounted_price


# ══════════════════════════════════════════════════════════════════════════════
# Model: shopee.order.escrow  –  Detail escrow (hanya order COMPLETED)
# ══════════════════════════════════════════════════════════════════════════════
class ShopeeOrderEscrow(models.Model):
    _name = "shopee.order.escrow"
    _description = "Shopee Escrow Detail"
    _rec_name = "order_id"

    order_id = fields.Many2one(
        "shopee.order", string="Order", ondelete="cascade", required=True
    )

    buyer_payment_amount = fields.Float(string="Buyer Payment Amount")
    actual_shipping_cost = fields.Float(string="Actual Shipping Cost")
    shopee_discount = fields.Float(string="Shopee Discount")
    voucher_from_seller = fields.Float(string="Voucher from Seller")
    coins_used = fields.Float(string="Coins Used")
    buyer_transaction_fee = fields.Float(string="Buyer Transaction Fee")
    commission_fee = fields.Float(string="Commission Fee")
    service_fee = fields.Float(string="Service Fee")
    seller_transaction_fee = fields.Float(string="Seller Transaction Fee")
    seller_lost_compensation = fields.Float(string="Seller Lost Compensation")
    seller_coin_cash_back = fields.Float(string="Seller Coin Cash Back")
    escrow_tax = fields.Float(string="Escrow Tax")
    final_escrow_amount = fields.Float(string="Final Escrow Amount")
    seller_income = fields.Float(string="Seller Income")

    bank_account_type = fields.Char(string="Bank Name")
    bank_account_number = fields.Char(string="Account Number")
    bank_account_name = fields.Char(string="Account Holder Name")

    escrow_release_time = fields.Integer(string="Escrow Release Time (Unix)")


# ══════════════════════════════════════════════════════════════════════════════
# Cron & manual sync  –  method dipanggil oleh ir.cron (data/shopee_cron.xml)
# ══════════════════════════════════════════════════════════════════════════════
# Method ini ditaruh di ShopeeConfig karena ir.cron butuh model sebagai target.
# Semua logic sync ada di ShopeeDataService.sync_orders_to_db()

# Catatan: import ShopeeDataService di sini menggunakan deferred import
# untuk menghindari circular dependency saat Odoo load modul.

class _ShopeeSync(models.Model):
    """
    Mixin model untuk cron & manual sync.
    Di-inherit oleh ShopeeConfig melalui _inherit.
    """
    _inherit = "shopee.config"

    def cron_sync_orders(self):
        """
        Dipanggil otomatis oleh ir.cron setiap X menit.
        Mengambil data order dari Shopee (atau dummy) dan menyimpan ke DB.
        """
        from ..services.shopee_service import ShopeeDataService

        _logger.info("[Shopee Cron] Memulai sinkronisasi order...")
        try:
            svc = ShopeeDataService(env=self.env)
            result = svc.sync_orders_to_db(self.env)
            _logger.info(
                "[Shopee Cron] Selesai: created=%d updated=%d errors=%d",
                result["created"], result["updated"], result["errors"],
            )
            # Update last_sync pada semua config aktif
            self.env["shopee.config"].sudo().search(
                [("active", "=", True)]
            ).write({"last_sync": fields.Datetime.now()})
        except Exception as exc:
            _logger.exception("[Shopee Cron] Error saat sync: %s", exc)

    def action_manual_sync(self):
        """
        Tombol 'Sync Now' dari UI Odoo – memanggil cron_sync_orders secara manual.
        Return action notifikasi agar muncul di layar.
        """
        self.cron_sync_orders()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Shopee Sync",
                "message": "Sinkronisasi order selesai. Cek log untuk detail.",
                "type": "success",
                "sticky": False,
            },
        }
