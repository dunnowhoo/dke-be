# -*- coding: utf-8 -*-

# ──────────────────────────────────────────────────────────────────────────────
# services/shopee_service.py
#
# Arsitektur:
#   ShopeeClient      – low-level HTTP client ke Shopee Open API
#   ShopeeDataService – orchestrator: pilih dummy vs live, transformasi data
#
# Dipanggil oleh:
#   controllers/shopee_controller.py       (REST API → Frontend)
#   controllers/shopee_webhook_controller.py (Push notification ← Shopee)
# ──────────────────────────────────────────────────────────────────────────────

import logging
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Custom Exception untuk Shopee API errors
# Shopee sering mengembalikan HTTP 200 dengan body {"error": "...", "message": "..."}
# ══════════════════════════════════════════════════════════════════════════════

class ShopeeApiError(Exception):
    """Raised ketika Shopee API mengembalikan error di response body."""

    def __init__(self, error_code: str, message: str, request_id: str = ""):
        self.error_code = error_code
        self.shopee_message = message
        self.request_id = request_id
        super().__init__(f"Shopee API error [{error_code}]: {message}")

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "message": self.shopee_message,
            "request_id": self.request_id,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DUMMY DATA
# Merepresentasikan struktur response Shopee Open API v2 yang sesungguhnya.
# ══════════════════════════════════════════════════════════════════════════════

DUMMY_ORDER_LIST = [
    {"order_sn": "SHP-2024-00001", "order_status": "COMPLETED"},
    {"order_sn": "SHP-2024-00002", "order_status": "SHIPPED"},
    {"order_sn": "SHP-2024-00003", "order_status": "READY_TO_SHIP"},
    {"order_sn": "SHP-2024-00004", "order_status": "UNPAID"},
    {"order_sn": "SHP-2024-00005", "order_status": "CANCELLED"},
    {"order_sn": "SHP-2024-00006", "order_status": "COMPLETED"},
    {"order_sn": "SHP-2024-00007", "order_status": "PROCESSED"},
    {"order_sn": "SHP-2024-00008", "order_status": "IN_CANCEL"},
]

DUMMY_DETAILS = {
    "SHP-2024-00001": {
        "order_sn": "SHP-2024-00001",
        "order_status": "COMPLETED",
        "buyer_username": "john_doe_123",
        "buyer_user_id": 100001,
        "create_time": 1709251200,
        "update_time": 1709510400,
        "pay_time": 1709254800,
        "currency": "IDR",
        "total_amount": 250000.0,
        "estimated_shipping_fee": 15000.0,
        "actual_shipping_cost": 15000.0,
        "shipping_carrier": "J&T Express",
        "tracking_number": "JP1234567890",
        "note": "Tolong bungkus rapi ya kak",
        "recipient_address": {
            "name": "John Doe", "phone": "08123456789",
            "town": "Menteng", "district": "Jakarta Pusat",
            "city": "DKI Jakarta", "state": "DKI Jakarta",
            "zipcode": "10310",
            "full_address": "Jl. Imam Bonjol No. 12, Menteng, Jakarta Pusat, DKI Jakarta 10310",
        },
        "item_list": [{"item_id": 9001, "item_name": "Sepatu Sneakers Pria Casual",
            "item_sku": "SKU-SHOE-001", "model_id": 5001, "model_name": "Size 42 / Hitam",
            "model_sku": "SKU-SHOE-001-42-BLK", "quantity_purchased": 1,
            "original_price": 250000.0, "discounted_price": 225000.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_shoe.jpg"}}],
    },
    "SHP-2024-00002": {
        "order_sn": "SHP-2024-00002",
        "order_status": "SHIPPED",
        "buyer_username": "siti_rahayu",
        "buyer_user_id": 100002,
        "create_time": 1709337600, "update_time": 1709424000, "pay_time": 1709341200,
        "currency": "IDR", "total_amount": 180000.0,
        "estimated_shipping_fee": 12000.0, "actual_shipping_cost": 12000.0,
        "shipping_carrier": "SiCepat", "tracking_number": "SC9876543210", "note": "",
        "recipient_address": {
            "name": "Siti Rahayu", "phone": "08234567890",
            "town": "Kebon Jeruk", "district": "Jakarta Barat",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "11530",
            "full_address": "Jl. Panjang No. 45, Kebon Jeruk, Jakarta Barat 11530",
        },
        "item_list": [{"item_id": 9002, "item_name": "Tas Ransel Laptop 15 inch",
            "item_sku": "SKU-BAG-002", "model_id": 5002, "model_name": "Warna Abu-abu",
            "model_sku": "SKU-BAG-002-GREY", "quantity_purchased": 1,
            "original_price": 180000.0, "discounted_price": 162000.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_bag.jpg"}}],
    },
    "SHP-2024-00003": {
        "order_sn": "SHP-2024-00003",
        "order_status": "READY_TO_SHIP",
        "buyer_username": "budi_santoso",
        "buyer_user_id": 100003,
        "create_time": 1709424000, "update_time": 1709427600, "pay_time": 1709424000,
        "currency": "IDR", "total_amount": 95000.0,
        "estimated_shipping_fee": 10000.0, "actual_shipping_cost": 0.0,
        "shipping_carrier": "Anteraja", "tracking_number": "",
        "note": "Pesanan untuk hadiah ulang tahun",
        "recipient_address": {
            "name": "Budi Santoso", "phone": "08345678901",
            "town": "Cilandak", "district": "Jakarta Selatan",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "12430",
            "full_address": "Jl. TB Simatupang No. 78, Cilandak, Jakarta Selatan 12430",
        },
        "item_list": [{"item_id": 9003, "item_name": "Buku Python Programming",
            "item_sku": "SKU-BOOK-003", "model_id": 5003, "model_name": "Edisi ke-3",
            "model_sku": "SKU-BOOK-003-ED3", "quantity_purchased": 2,
            "original_price": 50000.0, "discounted_price": 47500.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_book.jpg"}}],
    },
    "SHP-2024-00004": {
        "order_sn": "SHP-2024-00004",
        "order_status": "UNPAID",
        "buyer_username": "dewi_lestari",
        "buyer_user_id": 100004,
        "create_time": 1709510400, "update_time": 1709510400, "pay_time": 0,
        "currency": "IDR", "total_amount": 450000.0,
        "estimated_shipping_fee": 20000.0, "actual_shipping_cost": 0.0,
        "shipping_carrier": "", "tracking_number": "", "note": "",
        "recipient_address": {
            "name": "Dewi Lestari", "phone": "08456789012",
            "town": "Cempaka Putih", "district": "Jakarta Pusat",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "10510",
            "full_address": "Jl. Cempaka Putih Tengah No. 5, Jakarta Pusat 10510",
        },
        "item_list": [{"item_id": 9004, "item_name": "Headphone Bluetooth Premium",
            "item_sku": "SKU-ELEC-004", "model_id": 5004, "model_name": "Warna Putih",
            "model_sku": "SKU-ELEC-004-WHT", "quantity_purchased": 1,
            "original_price": 450000.0, "discounted_price": 405000.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_headphone.jpg"}}],
    },
    "SHP-2024-00005": {
        "order_sn": "SHP-2024-00005",
        "order_status": "CANCELLED",
        "buyer_username": "andi_wijaya",
        "buyer_user_id": 100005,
        "create_time": 1709164800, "update_time": 1709200000, "pay_time": 0,
        "currency": "IDR", "total_amount": 75000.0,
        "estimated_shipping_fee": 8000.0, "actual_shipping_cost": 0.0,
        "shipping_carrier": "", "tracking_number": "",
        "note": "Dibatalkan oleh pembeli",
        "recipient_address": {
            "name": "Andi Wijaya", "phone": "08567890123",
            "town": "Penjaringan", "district": "Jakarta Utara",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "14440",
            "full_address": "Jl. Pluit Raya No. 23, Penjaringan, Jakarta Utara 14440",
        },
        "item_list": [{"item_id": 9005, "item_name": "Kaos Polo Pria",
            "item_sku": "SKU-CLO-005", "model_id": 5005, "model_name": "Size M / Biru Navy",
            "model_sku": "SKU-CLO-005-M-NVY", "quantity_purchased": 2,
            "original_price": 40000.0, "discounted_price": 37500.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_polo.jpg"}}],
    },
    "SHP-2024-00006": {
        "order_sn": "SHP-2024-00006",
        "order_status": "COMPLETED",
        "buyer_username": "nina_kartika",
        "buyer_user_id": 100006,
        "create_time": 1709078400, "update_time": 1709337600, "pay_time": 1709082000,
        "currency": "IDR", "total_amount": 320000.0,
        "estimated_shipping_fee": 18000.0, "actual_shipping_cost": 18000.0,
        "shipping_carrier": "J&T Express", "tracking_number": "JP0987654321", "note": "",
        "recipient_address": {
            "name": "Nina Kartika", "phone": "08678901234",
            "town": "Tebet", "district": "Jakarta Selatan",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "12810",
            "full_address": "Jl. Tebet Raya No. 99, Tebet, Jakarta Selatan 12810",
        },
        "item_list": [{"item_id": 9006, "item_name": "Skincare Set Premium",
            "item_sku": "SKU-SKIN-006", "model_id": 5006, "model_name": "Paket Lengkap",
            "model_sku": "SKU-SKIN-006-FULL", "quantity_purchased": 1,
            "original_price": 320000.0, "discounted_price": 288000.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_skincare.jpg"}}],
    },
    "SHP-2024-00007": {
        "order_sn": "SHP-2024-00007",
        "order_status": "PROCESSED",
        "buyer_username": "reza_purnama",
        "buyer_user_id": 100007,
        "create_time": 1709596800, "update_time": 1709600400, "pay_time": 1709596800,
        "currency": "IDR", "total_amount": 550000.0,
        "estimated_shipping_fee": 25000.0, "actual_shipping_cost": 0.0,
        "shipping_carrier": "JNE", "tracking_number": "",
        "note": "Drop ship atas nama toko",
        "recipient_address": {
            "name": "Reza Purnama", "phone": "08789012345",
            "town": "Pancoran", "district": "Jakarta Selatan",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "12780",
            "full_address": "Jl. Raya Pasar Minggu No. 56, Pancoran, Jakarta Selatan 12780",
        },
        "item_list": [{"item_id": 9007, "item_name": "Smart Watch Android",
            "item_sku": "SKU-ELEC-007", "model_id": 5007, "model_name": "Warna Hitam / 44mm",
            "model_sku": "SKU-ELEC-007-BLK-44", "quantity_purchased": 1,
            "original_price": 550000.0, "discounted_price": 495000.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_watch.jpg"}}],
    },
    "SHP-2024-00008": {
        "order_sn": "SHP-2024-00008",
        "order_status": "IN_CANCEL",
        "buyer_username": "maya_sari",
        "buyer_user_id": 100008,
        "create_time": 1709683200, "update_time": 1709686800, "pay_time": 1709683200,
        "currency": "IDR", "total_amount": 125000.0,
        "estimated_shipping_fee": 9000.0, "actual_shipping_cost": 0.0,
        "shipping_carrier": "SiCepat", "tracking_number": "",
        "note": "Pembeli mengajukan pembatalan",
        "recipient_address": {
            "name": "Maya Sari", "phone": "08890123456",
            "town": "Tanjung Priok", "district": "Jakarta Utara",
            "city": "DKI Jakarta", "state": "DKI Jakarta", "zipcode": "14310",
            "full_address": "Jl. Enggano No. 11, Tanjung Priok, Jakarta Utara 14310",
        },
        "item_list": [{"item_id": 9008, "item_name": "Sepatu Sandal Wanita",
            "item_sku": "SKU-SHOE-008", "model_id": 5008, "model_name": "Size 38 / Coklat",
            "model_sku": "SKU-SHOE-008-38-BRN", "quantity_purchased": 1,
            "original_price": 125000.0, "discounted_price": 112500.0,
            "image_info": {"image_url": "https://cf.shopee.co.id/file/dummy_sandal.jpg"}}],
    },
}

# Dummy escrow hanya untuk order COMPLETED
DUMMY_ESCROW = {
    "SHP-2024-00001": {
        "order_sn": "SHP-2024-00001",
        "buyer_payment_amount": 250000.0, "actual_shipping_cost": 15000.0,
        "shopee_discount": 0.0, "voucher_from_seller": 0.0, "coins_used": 0.0,
        "buyer_transaction_fee": 2500.0, "commission_fee": 10000.0,
        "service_fee": 5000.0, "seller_transaction_fee": 2500.0,
        "seller_lost_compensation": 0.0, "seller_coin_cash_back": 0.0,
        "escrow_tax": 0.0, "final_escrow_amount": 215000.0,
        "order_income": {"escrow_amount": 215000.0, "seller_income": 215000.0},
        "bank_account": {"account_type": "BCA", "account_number": "****5678",
                         "account_name": "PT DKE SMART SALES"},
        "escrow_release_time": 1709683200,
    },
    "SHP-2024-00006": {
        "order_sn": "SHP-2024-00006",
        "buyer_payment_amount": 320000.0, "actual_shipping_cost": 18000.0,
        "shopee_discount": 0.0, "voucher_from_seller": 10000.0, "coins_used": 0.0,
        "buyer_transaction_fee": 3200.0, "commission_fee": 12800.0,
        "service_fee": 6400.0, "seller_transaction_fee": 3200.0,
        "seller_lost_compensation": 0.0, "seller_coin_cash_back": 0.0,
        "escrow_tax": 0.0, "final_escrow_amount": 269400.0,
        "order_income": {"escrow_amount": 269400.0, "seller_income": 269400.0},
        "bank_account": {"account_type": "BCA", "account_number": "****5678",
                         "account_name": "PT DKE SMART SALES"},
        "escrow_release_time": 1709769600,
    },
}

# Status yang tidak diperhitungkan sebagai pendapatan
NON_REVENUE_STATUSES = {"CANCELLED", "IN_CANCEL", "UNPAID"}


# ══════════════════════════════════════════════════════════════════════════════
# ShopeeClient  –  Low-level HTTP client ke Shopee Open API v2
# ══════════════════════════════════════════════════════════════════════════════

class ShopeeClient:
    """
    Menangani semua komunikasi langsung dengan Shopee Open API:
      - Pembuatan signature HMAC-SHA256
      - Penyusunan common query params
      - HTTP GET ke endpoint Shopee
    """

    BASE_SANDBOX = "https://partner.test-stable.shopeemobile.com"
    BASE_PROD = "https://partner.shopeemobile.com"

    def __init__(self, partner_id, partner_key, shop_id, access_token, sandbox=True):
        self.partner_id = str(partner_id)
        self.partner_key = partner_key
        self.shop_id = int(shop_id)
        self.access_token = access_token
        self.base_url = self.BASE_SANDBOX if sandbox else self.BASE_PROD

    # ── Signature ────────────────────────────────────────────────────────────
    def _sign(self, path: str, timestamp: int) -> str:
        """Hasilkan HMAC-SHA256 sesuai spesifikasi Shopee Open API v2."""
        import hashlib
        import hmac as _hmac
        base = f"{self.partner_id}{path}{timestamp}{self.access_token}{self.shop_id}"
        return _hmac.new(
            self.partner_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── Common params ─────────────────────────────────────────────────────────
    def _params(self, path: str) -> dict:
        import time as _time
        ts = int(_time.time())
        return {
            "partner_id":   int(self.partner_id),
            "timestamp":    ts,
            "access_token": self.access_token,
            "shop_id":      self.shop_id,
            "sign":         self._sign(path, ts),
        }

    # ── Response checker ──────────────────────────────────────────────────────
    @staticmethod
    def _check_response(data: dict) -> dict:
        """Cek apakah response Shopee mengandung error.

        Shopee mengembalikan HTTP 200 dengan body seperti:
          {"error": "order.order_list_invalid_time",
           "message": "Start time must be earlier than end time...",
           "request_id": "..."}

        Raises ShopeeApiError jika field 'error' ada dan bukan kosong.
        """
        error_code = data.get("error", "")
        if error_code:
            raise ShopeeApiError(
                error_code=error_code,
                message=data.get("message", "Unknown Shopee error"),
                request_id=data.get("request_id", ""),
            )
        return data

    # ── GET /api/v2/order/get_order_list ─────────────────────────────────────
    def get_order_list(self, time_from: int, time_to: int,
                       page_size: int = 50, cursor: str = "", order_status: str = "") -> dict:
        import requests as _req
        path = "/api/v2/order/get_order_list"
        params = self._params(path)
        params.update({
            "time_range_field": "create_time",
            "time_from":  time_from,
            "time_to":    time_to,
            "page_size":  page_size,
            "cursor":     cursor,
        })
        if order_status:
            params["order_status"] = order_status
        resp = _req.get(self.base_url + path, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._check_response(data)
        return data

    # ── GET /api/v2/order/get_order_detail ───────────────────────────────────
    def get_order_detail(self, order_sn_list: list) -> dict:
        import requests as _req
        path = "/api/v2/order/get_order_detail"
        params = self._params(path)
        params["order_sn_list"] = ",".join(order_sn_list)
        params["request_order_status_pending"] = "true"
        params["response_optional_fields"] = ",".join([
            "buyer_user_id",
            "buyer_username",
            "currency",
            "total_amount",
            "estimated_shipping_fee",
            "actual_shipping_cost",
            "pay_time",
            "shipping_carrier",
            "tracking_no",
            "note",
            "recipient_address",
            "item_list",
        ])
        resp = _req.get(self.base_url + path, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._check_response(data)
        return data

    # ── GET /api/v2/payment/get_escrow_detail ────────────────────────────────
    def get_escrow_detail(self, order_sn: str) -> dict:
        import requests as _req
        path = "/api/v2/payment/get_escrow_detail"
        params = self._params(path)
        params["order_sn"] = order_sn
        resp = _req.get(self.base_url + path, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._check_response(data)
        return data

    # ── Webhook signature verification ───────────────────────────────────────
    def verify_webhook(self, raw_body: bytes, shopee_signature: str) -> bool:
        """Verifikasi signature push notification dari Shopee."""
        import hashlib
        import hmac as _hmac
        computed = _hmac.new(
            self.partner_key.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return computed == shopee_signature


# ══════════════════════════════════════════════════════════════════════════════
# ShopeeDataService  –  Orchestrator: dummy vs live, transformasi data
#
# Ini adalah satu-satunya class yang dipanggil oleh controller.
# Controller tidak boleh tahu apakah data berasal dari dummy atau API live.
# ══════════════════════════════════════════════════════════════════════════════

class ShopeeDataService:
    """
    Business-logic layer untuk integrasi Shopee.

    Cara pakai dari controller:
        svc = ShopeeDataService(env=request.env)
        result = svc.get_order_list(page_size=10, cursor=0, status_filter="COMPLETED")

    Jika env tidak di-pass atau tidak ada ShopeeConfig aktif dengan use_dummy=False,
    service otomatis fallback ke dummy data.
    """
    _VALID_ORDER_STATUS = {
        "UNPAID", "READY_TO_SHIP", "PROCESSED", "SHIPPED",
        "TO_CONFIRM_RECEIVE", "COMPLETED", "IN_CANCEL", "CANCELLED",
        "TO_RETURN", "INVOICE_PENDING",
    }


    def __init__(self, env=None):
        self._client = None
        self._use_dummy = True
        self._env = env

        if env is None:
            return

        # Cari config aktif yang menggunakan live API
        config = env["shopee.config"].sudo().search(
            [("active", "=", True), ("use_dummy", "=", False)], limit=1
        )
        # Hanya aktifkan mode live jika kredensial lengkap
        if config and config.partner_id and config.partner_key \
                and config.shop_id and config.access_token:

            # ── Auto-refresh token jika hampir expired (< 10 menit) ──
            # error di-log di dalam method; lanjut dengan token lama jika gagal
            config.refresh_token_if_needed()

            self._client = ShopeeClient(
                partner_id=config.partner_id,
                partner_key=config.partner_key,
                shop_id=config.shop_id,
                access_token=config.access_token,
                sandbox=config.is_sandbox,
            )
            self._use_dummy = False
            _logger.info(
                "[ShopeeDataService] Mode LIVE – shop_id=%s sandbox=%s",
                config.shop_id, config.is_sandbox,
            )
        else:
            if config and not (config.shop_id and config.access_token):
                _logger.warning(
                    "[ShopeeDataService] Config ditemukan tapi kredensial belum lengkap "
                    "(shop_id=%s, has_token=%s). Fallback ke DUMMY.",
                    config.shop_id, bool(config.access_token),
                )
            _logger.info("[ShopeeDataService] Mode DUMMY – tidak ada ShopeeConfig live aktif.")

    # ── Helpers transformasi ──────────────────────────────────────────────────

    @staticmethod
    def _enrich_detail(raw: dict) -> dict:
        """
        Flatten recipient_address, tambahkan ISO timestamp, hitung subtotal item.
        Dipanggil sebelum data dikembalikan ke controller.
        """
        d = dict(raw)

        addr = d.get("recipient_address") or {}
        d["recipient_name"] = addr.get("name", "")
        d["recipient_phone"] = addr.get("phone", "")
        d["recipient_full_address"] = addr.get("full_address", "")
        d["recipient_town"] = addr.get("town", "")
        d["recipient_district"] = addr.get("district", "")
        d["recipient_city"] = addr.get("city", "")
        d["recipient_state"] = addr.get("state", "")
        d["recipient_zipcode"] = addr.get("zipcode", "")

        for ts_field in ("create_time", "update_time", "pay_time"):
            ts = d.get(ts_field, 0)
            d[f"{ts_field}_iso"] = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
            )

        for item in d.get("item_list", []):
            if "quantity_purchased" not in item:
                item["quantity_purchased"] = item.get("model_quantity_purchased", 0)
            if "discounted_price" not in item:
                item["discounted_price"] = item.get("model_discounted_price", 0)
            if "original_price" not in item:
                item["original_price"] = item.get("model_original_price", 0)
            item["subtotal"] = (
                item.get("quantity_purchased", 0) * item.get("discounted_price", 0)
            )

        return d

    @staticmethod
    def _enrich_escrow(raw: dict) -> dict:
        """Tambahkan ISO timestamp ke data escrow."""
        e = dict(raw)
        release = e.get("escrow_release_time", 0)
        e["escrow_release_time_iso"] = (
            datetime.fromtimestamp(release, tz=timezone.utc).isoformat() if release else None
        )
        return e

    # ── Public API (dipanggil oleh controller) ────────────────────────────────

    def get_order_list(self, page_size: int = 10, cursor: int = 0,
                       status_filter: str = "") -> dict:
        """
        Ambil daftar order dengan paginasi dan filter status.
        Mapping: GET /api/v2/order/get_order_list
        """
        if not self._use_dummy:
            import time
            now = int(time.time())
            live_status = status_filter.strip().upper()
            if live_status and live_status not in self._VALID_ORDER_STATUS:
                raise ValueError(f"order_status tidak valid: {status_filter}")
            # ShopeeApiError akan otomatis naik ke caller (controller)
            # jika Shopee mengembalikan error di response body.
            raw = self._client.get_order_list(
                # Shopee membatasi rentang query maksimal 15 hari.
                time_from=now - 14 * 24 * 3600,
                time_to=now,
                page_size=page_size,
                cursor=str(cursor),
                order_status=live_status,
            )
            return raw.get("response", {})

        orders = list(DUMMY_ORDER_LIST)
        if status_filter:
            orders = [o for o in orders if o["order_status"] == status_filter.upper()]

        total = len(orders)
        page_data = orders[cursor: cursor + page_size]
        has_more = (cursor + page_size) < total

        return {
            "order_list": page_data,
            "more": has_more,
            "next_cursor": str(cursor + page_size) if has_more else "",
            "total_count": total,
        }

    def get_order_detail(self, order_sn: str) -> dict | None:
        """
        Ambil detail satu order beserta escrow (jika COMPLETED).
        Mapping: GET /api/v2/order/get_order_detail
                 GET /api/v2/payment/get_escrow_detail (jika COMPLETED)
        """
        if not self._use_dummy:
            # ── LIVE ──────────────────────────────────────────────────────────
            # ShopeeApiError dibiarkan naik ke controller agar pesan error
            # Shopee bisa diteruskan ke frontend.
            try:
                raw = self._client.get_order_detail([order_sn])
                order_list = raw.get("response", {}).get("order_list", [])
                if not order_list:
                    _logger.warning("[ShopeeDataService] get_order_detail: empty response for %s", order_sn)
                    return None
                detail = self._enrich_detail(order_list[0])
                if detail.get("order_status") == "COMPLETED":
                    escrow_raw = self._client.get_escrow_detail(order_sn)
                    esc_resp = escrow_raw.get("response")
                    detail["escrow_detail"] = self._enrich_escrow(esc_resp) if esc_resp else None
                else:
                    detail["escrow_detail"] = None
                return detail
            except ShopeeApiError:
                raise  # biarkan naik ke controller
            except Exception as exc:
                _logger.error("[ShopeeDataService] get_order_detail LIVE error for %s: %s", order_sn, exc)
                return None
            # ─────────────────────────────────────────────────────────────────

        # ── DUMMY ─────────────────────────────────────────────────────────
        raw = DUMMY_DETAILS.get(order_sn)
        if not raw:
            return None

        detail = self._enrich_detail(raw)
        if detail.get("order_status") == "COMPLETED":
            raw_escrow = DUMMY_ESCROW.get(order_sn)
            detail["escrow_detail"] = self._enrich_escrow(raw_escrow) if raw_escrow else None
        else:
            detail["escrow_detail"] = None

        return detail

    def get_escrow_detail(self, order_sn: str) -> tuple[dict | None, str | None]:
        """
        Ambil escrow detail satu order.
        Mapping: GET /api/v2/payment/get_escrow_detail

        Return: (escrow_dict, error_message)
          - error_message berisi keterangan jika order tidak ditemukan / bukan COMPLETED
        """
        raw_order = DUMMY_DETAILS.get(order_sn) if self._use_dummy else None

        if self._use_dummy and not raw_order:
            return None, f"Order '{order_sn}' tidak ditemukan."

        status = (raw_order or {}).get("order_status", "")

        if self._use_dummy and status != "COMPLETED":
            return None, (
                f"Escrow hanya tersedia untuk order COMPLETED. "
                f"Status saat ini: {status}"
            )

        if not self._use_dummy:
            try:
                raw = self._client.get_escrow_detail(order_sn)
            except ShopeeApiError as e:
                return None, f"Shopee API error: {e.shopee_message}"
            escrow = raw.get("response")
            if not escrow:
                return None, "Data escrow tidak tersedia dari Shopee API."
            return self._enrich_escrow(escrow), None

        raw_escrow = DUMMY_ESCROW.get(order_sn)
        if not raw_escrow:
            return None, f"Data escrow untuk order '{order_sn}' belum tersedia."

        return self._enrich_escrow(raw_escrow), None

    def get_orders_bulk(self, order_sns: list) -> list:
        """
        Ambil detail beberapa order sekaligus.
        Mapping: GET /api/v2/order/get_order_detail (batch)

        Jika order_sns kosong:
          - Mode dummy → ambil semua dari DUMMY_DETAILS
          - Mode live  → paginate order_list terlebih dahulu untuk kumpulkan order_sn
        """
        if not order_sns:
            if self._use_dummy:
                order_sns = list(DUMMY_DETAILS.keys())
            else:
                # Paginate get_order_list untuk kumpulkan semua order_sn
                order_sns = self._fetch_all_order_sns()
                if not order_sns:
                    _logger.info("[ShopeeDataService] get_orders_bulk: no order_sns from live API.")
                    return []

        if not self._use_dummy:
            return self._fetch_details_chunked(order_sns)

        result = []
        for sn in order_sns:
            raw = DUMMY_DETAILS.get(sn)
            if not raw:
                continue
            enriched = self._enrich_detail(raw)
            if enriched.get("order_status") == "COMPLETED":
                raw_escrow = DUMMY_ESCROW.get(sn)
                enriched["escrow_detail"] = (
                    self._enrich_escrow(raw_escrow) if raw_escrow else None
                )
            else:
                enriched["escrow_detail"] = None
            result.append(enriched)

        return result

    def get_summary(self) -> dict:
        """
        Hitung statistik ringkasan untuk dashboard.
        Mode live: baca dari cache DB (shopee.order) yang sudah di-sync.
        """
        if not self._use_dummy and self._env:
            Order = self._env["shopee.order"].sudo()
            orders = Order.search([])
            status_count = {}
            total_revenue = 0.0
            total_escrow = 0.0
            for o in orders:
                st = o.order_status or "UNKNOWN"
                status_count[st] = status_count.get(st, 0) + 1
                if st not in NON_REVENUE_STATUSES:
                    total_revenue += o.total_amount or 0.0
                if st == "COMPLETED":
                    for esc in o.escrow_id:
                        total_escrow += esc.final_escrow_amount or 0.0
            return {
                "total_orders": len(orders),
                "status_breakdown": status_count,
                "total_revenue_idr": total_revenue,
                "total_escrow_received_idr": total_escrow,
                "currency": "IDR",
            }

        status_count = {}
        total_revenue = 0.0
        total_escrow = 0.0

        for sn, detail in DUMMY_DETAILS.items():
            status = detail.get("order_status", "UNKNOWN")
            status_count[status] = status_count.get(status, 0) + 1
            if status not in NON_REVENUE_STATUSES:
                total_revenue += detail.get("total_amount", 0.0)
            if status == "COMPLETED":
                escrow = DUMMY_ESCROW.get(sn)
                if escrow:
                    total_escrow += escrow.get("final_escrow_amount", 0.0)

        return {
            "total_orders": len(DUMMY_DETAILS),
            "status_breakdown": status_count,
            "total_revenue_idr": total_revenue,
            "total_escrow_received_idr": total_escrow,
            "currency": "IDR",
        }

    # ── Helper methods untuk live API ─────────────────────────────────────────

    def _fetch_all_order_sns(self, days_back: int = 14) -> list:
        """
        Paginate GET /api/v2/order/get_order_list dan kumpulkan semua order_sn.
        Shopee membatasi max 50 per page; method ini loop sampai `more=False`.
        """
        import time as _time
        now = int(_time.time())
        # Shopee mensyaratkan range get_order_list tidak lebih dari 15 hari.
        safe_days_back = max(1, min(days_back, 14))
        time_from = now - safe_days_back * 24 * 3600

        all_sns: list[str] = []
        cursor = ""
        max_pages = 100  # safety limit

        for _ in range(max_pages):
            try:
                raw = self._client.get_order_list(
                    time_from=time_from,
                    time_to=now,
                    page_size=50,
                    cursor=cursor,
                )
                response = raw.get("response", {})
                order_list = response.get("order_list") or []
                for o in order_list:
                    sn = o.get("order_sn", "")
                    if sn:
                        all_sns.append(sn)

                if not response.get("more", False):
                    break
                cursor = response.get("next_cursor", "")
                if not cursor:
                    break
            except ShopeeApiError:
                raise  # biarkan naik ke controller
            except Exception as exc:
                _logger.error("[ShopeeDataService] _fetch_all_order_sns error: %s", exc)
                break

        _logger.info("[ShopeeDataService] _fetch_all_order_sns: collected %d order_sn(s)", len(all_sns))
        return all_sns

    def _fetch_details_chunked(self, order_sns: list, chunk_size: int = 50) -> list:
        """
        Ambil order detail secara batch (Shopee max 50 order_sn per call).
        Untuk setiap order COMPLETED, ambil juga escrow detail.
        """
        result: list[dict] = []
        for i in range(0, len(order_sns), chunk_size):
            chunk = order_sns[i:i + chunk_size]
            try:
                raw = self._client.get_order_detail(chunk)
                orders = raw.get("response", {}).get("order_list", [])
            except ShopeeApiError:
                raise  # biarkan naik ke controller
            except Exception as exc:
                _logger.error(
                    "[ShopeeDataService] _fetch_details_chunked error on chunk %d-%d: %s",
                    i, i + len(chunk), exc,
                )
                continue

            for o in orders:
                enriched = self._enrich_detail(o)
                if enriched.get("order_status") == "COMPLETED":
                    try:
                        esc_raw = self._client.get_escrow_detail(enriched["order_sn"])
                        esc_resp = esc_raw.get("response")
                        enriched["escrow_detail"] = self._enrich_escrow(esc_resp) if esc_resp else None
                    except Exception as exc:
                        _logger.error(
                            "[ShopeeDataService] escrow error for %s: %s",
                            enriched["order_sn"], exc,
                        )
                        enriched["escrow_detail"] = None
                else:
                    enriched["escrow_detail"] = None
                result.append(enriched)

        _logger.info("[ShopeeDataService] _fetch_details_chunked: fetched %d order detail(s)", len(result))
        return result

    # ── Sync to DB ────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_order(env, order_data: dict) -> str:
        """
        Upsert satu order ke DB (shopee.order, shopee.order.item, shopee.order.escrow).

        Return: "created" | "updated"
        Raises: Exception jika gagal.
        """
        Order = env["shopee.order"].sudo()
        Item = env["shopee.order.item"].sudo()
        Escrow = env["shopee.order.escrow"].sudo()

        order_sn = order_data.get("order_sn", "")
        if not order_sn:
            raise ValueError("order_sn kosong")

        addr = order_data.get("recipient_address") or {}
        order_vals = {
            "order_sn":               order_sn,
            "order_status":           order_data.get("order_status", ""),
            "buyer_username":          order_data.get("buyer_username", ""),
            "buyer_user_id":           str(order_data.get("buyer_user_id") or ""),
            "total_amount":            order_data.get("total_amount", 0.0),
            "currency":                order_data.get("currency", "IDR"),
            "estimated_shipping_fee":  order_data.get("estimated_shipping_fee", 0.0),
            "actual_shipping_cost":    order_data.get("actual_shipping_cost", 0.0),
            "create_time":             order_data.get("create_time", 0),
            "update_time":             order_data.get("update_time", 0),
            "pay_time":                order_data.get("pay_time", 0),
            "shipping_carrier":        order_data.get("shipping_carrier", ""),
            "tracking_number":         order_data.get("tracking_number") or order_data.get("tracking_no", ""),
            "note":                    order_data.get("note", ""),
            "recipient_name":          order_data.get("recipient_name") or addr.get("name", ""),
            "recipient_phone":         order_data.get("recipient_phone") or addr.get("phone", ""),
            "recipient_full_address":  order_data.get("recipient_full_address") or addr.get("full_address", ""),
            "recipient_town":          order_data.get("recipient_town") or addr.get("town", ""),
            "recipient_district":      order_data.get("recipient_district") or addr.get("district", ""),
            "recipient_city":          order_data.get("recipient_city") or addr.get("city", ""),
            "recipient_state":         order_data.get("recipient_state") or addr.get("state", ""),
            "recipient_zipcode":       order_data.get("recipient_zipcode") or addr.get("zipcode", ""),
        }

        existing = Order.search([("order_sn", "=", order_sn)], limit=1)
        if existing:
            existing.write(order_vals)
            order_rec = existing
            action = "updated"
        else:
            order_rec = Order.create(order_vals)
            action = "created"

        # ── Sync item_list ────────────────────────────────────────────
        order_rec.order_item_ids.unlink()
        for item in order_data.get("item_list", []):
            img = (item.get("image_info") or {}).get("image_url", "")
            Item.create({
                "order_id":           order_rec.id,
                "item_id":            str(item.get("item_id") or ""),
                "item_name":          item.get("item_name", ""),
                "item_sku":           item.get("item_sku", ""),
                "model_id":           str(item.get("model_id") or ""),
                "model_name":         item.get("model_name", ""),
                "model_sku":          item.get("model_sku", ""),
                "quantity_purchased": item.get("quantity_purchased", 1),
                "original_price":     item.get("original_price", 0.0),
                "discounted_price":   item.get("discounted_price", 0.0),
                "image_url":          img,
            })

        # ── Sync escrow (hanya COMPLETED) ─────────────────────────────
        if order_data.get("order_status") == "COMPLETED":
            escrow_data = order_data.get("escrow_detail") or {}
            if escrow_data:
                bank = escrow_data.get("bank_account") or {}
                income = escrow_data.get("order_income") or {}
                escrow_vals = {
                    "order_id":               order_rec.id,
                    "buyer_payment_amount":    escrow_data.get("buyer_payment_amount", 0.0),
                    "actual_shipping_cost":    escrow_data.get("actual_shipping_cost", 0.0),
                    "shopee_discount":         escrow_data.get("shopee_discount", 0.0),
                    "voucher_from_seller":     escrow_data.get("voucher_from_seller", 0.0),
                    "coins_used":              escrow_data.get("coins_used", 0.0),
                    "buyer_transaction_fee":   escrow_data.get("buyer_transaction_fee", 0.0),
                    "commission_fee":          escrow_data.get("commission_fee", 0.0),
                    "service_fee":             escrow_data.get("service_fee", 0.0),
                    "seller_transaction_fee":  escrow_data.get("seller_transaction_fee", 0.0),
                    "seller_lost_compensation":escrow_data.get("seller_lost_compensation", 0.0),
                    "seller_coin_cash_back":   escrow_data.get("seller_coin_cash_back", 0.0),
                    "escrow_tax":              escrow_data.get("escrow_tax", 0.0),
                    "final_escrow_amount":     escrow_data.get("final_escrow_amount", 0.0),
                    "seller_income":           income.get("seller_income", 0.0),
                    "bank_account_type":       bank.get("account_type", ""),
                    "bank_account_number":     bank.get("account_number", ""),
                    "bank_account_name":       bank.get("account_name", ""),
                    "escrow_release_time":     escrow_data.get("escrow_release_time", 0),
                }
                existing_escrow = Escrow.search(
                    [("order_id", "=", order_rec.id)], limit=1
                )
                if existing_escrow:
                    existing_escrow.write(escrow_vals)
                else:
                    Escrow.create(escrow_vals)

        return action

    def sync_orders_to_db(self, env) -> dict:
        """
        Ambil semua order dari sumber data (dummy / live) lalu simpan/update
        ke tabel database Odoo (shopee.order, shopee.order.item, shopee.order.escrow).

        Return: { "created": int, "updated": int, "errors": int, "source": "live"|"dummy" }
        """
        _logger.info(
            "[ShopeeDataService] sync_orders_to_db dimulai – mode=%s",
            "LIVE" if not self._use_dummy else "DUMMY",
        )
        orders = self.get_orders_bulk([])

        created = updated = errors = 0

        for order_data in orders:
            try:
                with env.cr.savepoint():
                    action = self._upsert_order(env, order_data)
                if action == "created":
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                _logger.error("sync_orders_to_db: error on order %s: %s",
                              order_data.get("order_sn", "?"), exc)
                errors += 1

        source = "live" if not self._use_dummy else "dummy"
        _logger.info(
            "Shopee sync done: source=%s created=%d updated=%d errors=%d",
            source, created, updated, errors,
        )
        return {"created": created, "updated": updated, "errors": errors, "source": source}

    def sync_single_order(self, env, order_sn: str) -> dict:
        """
        Ambil detail satu order dari sumber data (dummy / live), lalu upsert ke DB.

        Return: { "status": "created"|"updated", "order_sn": str }
        Raises: ValueError jika order_sn tidak ditemukan.
        """
        _logger.info(
            "[ShopeeDataService] sync_single_order: %s (mode=%s)",
            order_sn, "LIVE" if not self._use_dummy else "DUMMY",
        )

        detail = self.get_order_detail(order_sn)
        if detail is None:
            raise ValueError(f"Order '{order_sn}' tidak ditemukan di Shopee.")

        action = self._upsert_order(env, detail)
        _logger.info(
            "[ShopeeDataService] sync_single_order: %s → %s", order_sn, action
        )
        return {"status": action, "order_sn": order_sn}
