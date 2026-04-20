# -*- coding: utf-8 -*-

import json
import logging

import werkzeug.exceptions

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ProductsController(http.Controller):
    """REST API endpoints for Product management.

    Consumed by propenheimer-frontend /sales/products.
    Role guard: sales_manager and admin only.
    Uses Odoo built-in model product.product.
    """

    def _require_access(self):
        user = request.env.user
        if not (
            user.has_group('dke_crm.group_sales_manager')
            or user.has_group('base.group_system')
        ):
            raise werkzeug.exceptions.Forbidden(
                'Akses ditolak. Hanya Sales Manager atau Admin yang diizinkan.'
            )

    def _product_to_dict(self, p):
        return {
            'id': p.id,
            'name': p.name,
            'price': p.lst_price,
            'description': p.description_sale or '',
            'active': p.active,
            'category_id': p.categ_id.id if p.categ_id else None,
            'category_name': p.categ_id.name if p.categ_id else None,
        }

    # ======================================================================
    # GET /api/products — list with pagination + search
    # ======================================================================

    @http.route(
        '/api/products',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_products(self, **kwargs):
        """GET /api/products — List produk dengan pagination dan search.

        Query Params:
            page    (int, default=1)
            limit   (int, default=20, max=100)
            search  (str)  filter by name
            active  (str)  'true'|'false'|'all'  default='true'

        Response 200:
        {
            "total": int,
            "page": int,
            "limit": int,
            "products": [{ id, name, price, description, active, category_id, category_name }]
        }
        """
        self._require_access()
        try:
            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)
            offset = (page - 1) * limit
            search_query = (kwargs.get('search') or '').strip()
            active_param = (kwargs.get('active') or 'true').lower()

            domain = [('sale_ok', '=', True)]

            if active_param == 'all':
                domain.append(('active', 'in', [True, False]))
            elif active_param == 'false':
                domain.append(('active', '=', False))
            else:
                domain.append(('active', '=', True))

            if search_query:
                domain.append(('name', 'ilike', search_query))

            Product = request.env['product.product'].with_context(active_test=False).sudo()
            total = Product.search_count(domain)
            products = Product.search(domain, limit=limit, offset=offset, order='name asc')

            return request.make_json_response({
                'total': total,
                'page': page,
                'limit': limit,
                'products': [self._product_to_dict(p) for p in products],
            })

        except werkzeug.exceptions.Forbidden as e:
            return request.make_json_response({'error': 'forbidden', 'message': str(e)}, status=403)
        except Exception as exc:
            _logger.exception('[Products] list_products error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500
            )

    # ======================================================================
    # GET /api/products/<id> — detail satu produk
    # ======================================================================

    @http.route(
        '/api/products/<int:product_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def get_product(self, product_id, **kwargs):
        """GET /api/products/{id} — Detail satu produk."""
        self._require_access()
        try:
            product = request.env['product.product'].with_context(active_test=False).sudo().browse(product_id)
            if not product.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Produk tidak ditemukan.'}, status=404
                )

            return request.make_json_response({'product': self._product_to_dict(product)})

        except werkzeug.exceptions.Forbidden as e:
            return request.make_json_response({'error': 'forbidden', 'message': str(e)}, status=403)
        except Exception as exc:
            _logger.exception('[Products] get_product error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500
            )

    # ======================================================================
    # POST /api/products — create produk baru
    # ======================================================================

    @http.route(
        '/api/products',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_product(self, **kwargs):
        """POST /api/products — Create produk baru.

        JSON Body: { name, price, description, active }
        Response 201: { product }
        """
        self._require_access()
        try:
            body = json.loads(request.httprequest.data or b'{}')

            name = (body.get('name') or '').strip()
            if not name:
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Nama produk wajib diisi.'}, status=400
                )

            raw_price = body.get('price', 0)
            try:
                price = float(raw_price)
                if price < 0:
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'Harga tidak boleh negatif.'}, status=400
                    )
            except (ValueError, TypeError):
                return request.make_json_response(
                    {'error': 'validation', 'message': 'Format harga tidak valid.'}, status=400
                )

            vals = {
                'name': name,
                'lst_price': price,
                'description_sale': body.get('description') or '',
                'sale_ok': True,
                'active': bool(body.get('active', True)),
            }

            product = request.env['product.product'].sudo().create(vals)

            return request.make_json_response({'product': self._product_to_dict(product)}, status=201)

        except werkzeug.exceptions.Forbidden as e:
            return request.make_json_response({'error': 'forbidden', 'message': str(e)}, status=403)
        except Exception as exc:
            _logger.exception('[Products] create_product error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500
            )

    # ======================================================================
    # PUT /api/products/<id> — update produk
    # ======================================================================

    @http.route(
        '/api/products/<int:product_id>',
        type='http',
        auth='user',
        methods=['PUT'],
        csrf=False,
        cors='*',
    )
    def update_product(self, product_id, **kwargs):
        """PUT /api/products/{id} — Update produk.

        JSON Body (semua opsional): { name, price, description, active }
        Response 200: { product }
        """
        self._require_access()
        try:
            body = json.loads(request.httprequest.data or b'{}')

            product = request.env['product.product'].with_context(active_test=False).sudo().browse(product_id)
            if not product.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Produk tidak ditemukan.'}, status=404
                )

            vals = {}

            if 'name' in body:
                name = (body['name'] or '').strip()
                if not name:
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'Nama produk tidak boleh kosong.'}, status=400
                    )
                vals['name'] = name

            if 'price' in body:
                try:
                    price = float(body['price'])
                    if price < 0:
                        return request.make_json_response(
                            {'error': 'validation', 'message': 'Harga tidak boleh negatif.'}, status=400
                        )
                    vals['lst_price'] = price
                except (ValueError, TypeError):
                    return request.make_json_response(
                        {'error': 'validation', 'message': 'Format harga tidak valid.'}, status=400
                    )

            if 'description' in body:
                vals['description_sale'] = body['description'] or ''

            if 'active' in body:
                vals['active'] = bool(body['active'])

            if vals:
                product.write(vals)

            return request.make_json_response({'product': self._product_to_dict(product)})

        except werkzeug.exceptions.Forbidden as e:
            return request.make_json_response({'error': 'forbidden', 'message': str(e)}, status=403)
        except Exception as exc:
            _logger.exception('[Products] update_product error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500
            )

    # ======================================================================
    # DELETE /api/products/<id> — soft delete (active=False)
    # ======================================================================

    @http.route(
        '/api/products/<int:product_id>',
        type='http',
        auth='user',
        methods=['DELETE'],
        csrf=False,
        cors='*',
    )
    def delete_product(self, product_id, **kwargs):
        """DELETE /api/products/{id} — Soft delete produk (set active=False).

        Response 200: { message }
        """
        self._require_access()
        try:
            product = request.env['product.product'].with_context(active_test=False).sudo().browse(product_id)
            if not product.exists():
                return request.make_json_response(
                    {'error': 'not_found', 'message': 'Produk tidak ditemukan.'}, status=404
                )

            product.write({'active': False})

            return request.make_json_response({'message': f'Produk "{product.name}" berhasil dinonaktifkan.'})

        except werkzeug.exceptions.Forbidden as e:
            return request.make_json_response({'error': 'forbidden', 'message': str(e)}, status=403)
        except Exception as exc:
            _logger.exception('[Products] delete_product error: %s', exc)
            return request.make_json_response(
                {'error': 'server_error', 'message': str(exc)}, status=500
            )
