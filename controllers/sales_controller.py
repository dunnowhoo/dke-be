# -*- coding: utf-8 -*-

import json
import logging

import werkzeug.exceptions

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class SalesController(http.Controller):
    """REST API endpoints for Sales transactions and analytics.

    EPIC04 - PBI-9, PBI-10, PBI-11, PBI-12

    Endpoint ini dikonsumsi langsung oleh Next.js frontend.
    Semua data diambil dari DB lokal (shopee.order) yang sudah di-sync
    dari Shopee API oleh ShopeeDataService.
    """

    def _require_sales_access(self):
        """Raise 403 jika user bukan Admin atau Sales Manager."""
        user = request.env.user
        if not (
            user.has_group("dke_crm.group_sales_manager")
            or user.has_group("base.group_system")
        ):
            raise werkzeug.exceptions.Forbidden(
                "Akses ditolak. Hanya Sales Manager atau Admin yang diizinkan."
            )

    # ══════════════════════════════════════════════════════════════
    # SYNC (PBI-9)
    # ══════════════════════════════════════════════════════════════

    @http.route(
        "/api/sales/transactions/sync",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
        cors="*",
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def sync_transactions(self, **kwargs):
        """POST /api/sales/transactions/sync — Sync order dari Shopee ke DB lokal.

        PBI-9: Ambil data terbaru dari Shopee (atau dummy), upsert ke shopee.order.
        """
        self._require_sales_access()
        try:
            from ..services.shopee_service import ShopeeDataService, ShopeeApiError

            svc = ShopeeDataService(env=request.env)
            result = svc.sync_orders_to_db(request.env)

            # Update last_sync
            request.env["shopee.config"].sudo().search(
                [("active", "=", True)]
            ).write({"last_sync": fields.Datetime.now()})

            return request.make_json_response({
                "status": "success",
                "synced_count": result.get("created", 0) + result.get("updated", 0),
                "created": result.get("created", 0),
                "updated": result.get("updated", 0),
                "errors": result.get("errors", 0),
            })
        except ShopeeApiError as e:
            _logger.warning("[Sales] Sync Shopee API error: %s", e)
            return request.make_json_response(
                {"status": "error", "message": f"Shopee: {e.shopee_message}"}, status=502
            )
        except Exception as exc:
            _logger.exception("[Sales] Sync error: %s", exc)
            return request.make_json_response(
                {"status": "error", "message": str(exc)}, status=500
            )

    # ══════════════════════════════════════════════════════════════
    # SYNC SINGLE ORDER
    # ══════════════════════════════════════════════════════════════

    @http.route(
        "/api/sales/transactions/<string:order_sn>/sync",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
        cors="*",
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def sync_single_transaction(self, order_sn, **kwargs):
        """POST /api/sales/transactions/<order_sn>/sync — Sync satu order dari Shopee.

        Mengambil detail terbaru order dari Shopee API (atau dummy) berdasarkan
        order_sn, lalu upsert ke tabel shopee.order di DB lokal.
        """
        self._require_sales_access()
        try:
            from ..services.shopee_service import ShopeeDataService, ShopeeApiError

            svc = ShopeeDataService(env=request.env)
            result = svc.sync_single_order(request.env, order_sn)

            return request.make_json_response({
                "status": "success",
                "data": result,
                "message": f"Order {order_sn} berhasil di-update.",
            })
        except ValueError as ve:
            return request.make_json_response(
                {"status": "error", "message": str(ve)}, status=404
            )
        except ShopeeApiError as e:
            _logger.warning("[Sales] Sync single order Shopee API error: %s", e)
            return request.make_json_response(
                {"status": "error", "message": f"Shopee: {e.shopee_message}"}, status=502
            )
        except Exception as exc:
            _logger.exception("[Sales] Sync single order error: %s", exc)
            return request.make_json_response(
                {"status": "error", "message": str(exc)}, status=500
            )

    # ══════════════════════════════════════════════════════════════
    # LIST TRANSACTIONS (PBI-10)
    # ══════════════════════════════════════════════════════════════

    @http.route(
        "/api/sales/transactions",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
        cors="*",
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def list_transactions(self, **kwargs):
        """GET /api/sales/transactions — List transaksi dengan pagination & filter.

        PBI-10: Daftar order yang sudah tersimpan di DB.

        Query Params:
          - page       (int, default=1)
          - limit      (int, default=20)
          - status     (str, filter by order_status e.g. COMPLETED)
          - sort_by    (str, default=create_time, options: create_time|total_amount)
          - order      (str, default=desc)
          - search     (str, filter by order_sn or buyer_username)
        """
        self._require_sales_access()
        try:
            page = int(kwargs.get("page", 1))
            limit = min(int(kwargs.get("limit", 20)), 100)
            offset = (page - 1) * limit
            status_filter = kwargs.get("status")
            sort_by = kwargs.get("sort_by", "create_time")
            order_dir = kwargs.get("order", "desc")
            search_query = kwargs.get("search", "").strip()

            allowed_sort = {"create_time", "total_amount", "update_time"}
            if sort_by not in allowed_sort:
                sort_by = "create_time"
            if order_dir not in ("asc", "desc"):
                order_dir = "desc"

            domain = []
            if status_filter:
                domain.append(("order_status", "=", status_filter.upper()))
            if search_query:
                domain += [
                    "|",
                    ("order_sn", "ilike", search_query),
                    ("buyer_username", "ilike", search_query),
                ]

            Order = request.env["shopee.order"].sudo()
            total = Order.search_count(domain)
            orders = Order.search(
                domain,
                limit=limit,
                offset=offset,
                order=f"{sort_by} {order_dir}",
            )

            data = []
            for o in orders:
                data.append({
                    "id": o.id,
                    "order_sn": o.order_sn,
                    "order_status": o.order_status,
                    "buyer_username": o.buyer_username,
                    "total_amount": o.total_amount,
                    "currency": o.currency,
                    "create_time": o.create_time,
                    "update_time": o.update_time,
                    "shipping_carrier": o.shipping_carrier,
                    "tracking_number": o.tracking_number,
                    "recipient_name": o.recipient_name,
                    "recipient_city": o.recipient_city,
                    "note": o.note,
                    "items_count": len(o.order_item_ids),
                })

            return request.make_json_response({
                "status": "success",
                "data": data,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": -(-total // limit),  # ceiling division
                },
            })
        except Exception as exc:
            _logger.exception("[Sales] list_transactions error: %s", exc)
            return request.make_json_response(
                {"status": "error", "message": str(exc)}, status=500
            )

    # ══════════════════════════════════════════════════════════════
    # TRANSACTION DETAIL (PBI-11)
    # ══════════════════════════════════════════════════════════════

    @http.route(
        "/api/sales/transactions/<int:transaction_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
        cors="*",
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def get_transaction_detail(self, transaction_id, **kwargs):
        """GET /api/sales/transactions/{id} — Detail order lengkap dengan items & escrow.

        PBI-11: Termasuk line items, detail pengiriman, dan breakdown escrow.
        """
        self._require_sales_access()
        try:
            order = request.env["shopee.order"].sudo().browse(transaction_id)
            if not order.exists():
                return request.make_json_response(
                    {"status": "error", "message": "Order tidak ditemukan."}, status=404
                )

            # Items
            items = []
            for item in order.order_item_ids:
                items.append({
                    "id": item.id,
                    "item_id": item.item_id,
                    "item_name": item.item_name,
                    "item_sku": item.item_sku,
                    "model_name": item.model_name,
                    "model_sku": item.model_sku,
                    "quantity": item.quantity_purchased,
                    "original_price": item.original_price,
                    "discounted_price": item.discounted_price,
                    "subtotal": item.subtotal,
                    "image_url": item.image_url,
                })

            # Escrow (only if COMPLETED)
            escrow = None
            if order.escrow_id:
                esc = order.escrow_id[0]
                escrow = {
                    "buyer_payment_amount": esc.buyer_payment_amount,
                    "actual_shipping_cost": esc.actual_shipping_cost,
                    "shopee_discount": esc.shopee_discount,
                    "commission_fee": esc.commission_fee,
                    "service_fee": esc.service_fee,
                    "seller_transaction_fee": esc.seller_transaction_fee,
                    "final_escrow_amount": esc.final_escrow_amount,
                    "seller_income": esc.seller_income,
                    "bank_account_type": esc.bank_account_type,
                    "bank_account_number": esc.bank_account_number,
                    "escrow_release_time": esc.escrow_release_time,
                }

            data = {
                "id": order.id,
                "order_sn": order.order_sn,
                "order_status": order.order_status,
                "buyer_username": order.buyer_username,
                "buyer_user_id": order.buyer_user_id,
                "total_amount": order.total_amount,
                "currency": order.currency,
                "estimated_shipping_fee": order.estimated_shipping_fee,
                "actual_shipping_cost": order.actual_shipping_cost,
                "create_time": order.create_time,
                "update_time": order.update_time,
                "pay_time": order.pay_time,
                "shipping_carrier": order.shipping_carrier,
                "tracking_number": order.tracking_number,
                "recipient": {
                    "name": order.recipient_name,
                    "phone": order.recipient_phone,
                    "full_address": order.recipient_full_address,
                    "town": order.recipient_town,
                    "district": order.recipient_district,
                    "city": order.recipient_city,
                    "state": order.recipient_state,
                    "zipcode": order.recipient_zipcode,
                },
                "note": order.note,
                "dropshipper": order.dropshipper,
                "items_subtotal": order.items_subtotal,
                "items": items,
                "escrow": escrow,
            }

            return request.make_json_response({"status": "success", "data": data})
        except Exception as exc:
            _logger.exception("[Sales] get_transaction_detail error: %s", exc)
            return request.make_json_response(
                {"status": "error", "message": str(exc)}, status=500
            )

    # ══════════════════════════════════════════════════════════════
    # REVENUE ANALYTICS (PBI-12) — TODO
    # ══════════════════════════════════════════════════════════════

    @http.route(
        "/api/sales/analytics/revenue",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
        cors="*",
        groups="dke_crm.group_sales_manager,base.group_system",
    )
    def get_revenue_analytics(self, **kwargs):
        """GET /api/sales/analytics/revenue — Aggregasi revenue untuk grafik.

        PBI-12: Gross vs net revenue per hari/minggu/bulan.
        TODO: Implementasi belum dikerjakan.
        """
        self._require_sales_access()
        # TODO: Implement revenue analytics aggregation
        return request.make_json_response({
            "status": "ok",
            "labels": [],
            "gross_series": [],
            "net_series": [],
            "orders_series": [],
        })

