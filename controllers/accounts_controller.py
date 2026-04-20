# -*- coding: utf-8 -*-

import json
import logging
import math
from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

VALID_SPECIALIZATIONS = ('teknis', 'produk', 'pengiriman')


# ------------------------------------------------------------------ #
#  Helpers                                                              #
# ------------------------------------------------------------------ #

def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def _require_admin():
    """Return True if the current user is admin; return an error dict otherwise."""
    user = request.env.user
    is_admin = user._is_admin() or user.has_group('base.group_system')
    if not is_admin:
        return _error(403, 'Akses ditolak. Hanya admin yang dapat mengakses endpoint ini.')
    return None


def _serialize_user(user):
    """Convert res.users record to dict for API response."""
    return {
        'user_id': user.id,
        'name': user.name,
        'email': user.login,
        'phone': user.dke_phone or '',
        'role': user.dke_role,
        'status': user.dke_status or 'active',
        'specialization': user.dke_specialization or None,
        'created_at': user.create_date.isoformat() if user.create_date else None,
        'last_login': user.login_date.isoformat() if user.login_date else None,
    }


class AccountsController(http.Controller):
    """REST API endpoints for Account Management (PBI-3 to PBI-6)."""

    # ================================================================== #
    #  CUSTOMER CARE                                                       #
    # ================================================================== #

    # ------------------------------------------------------------------ #
    #  POST /api/accounts/customer-care  (PBI-3)                           #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/customer-care',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_customer_care(self, name='', email='', phone='', password='', **kwargs):
        """Create a new Customer Care account.

        JSON-RPC params:
            { "name": "...", "email": "...", "phone": "...", "password": "..." }

        Response 201:
            { "status": "success", "data": { user_id, name, email, role } }

        Response 409:
            { "status": "error", "message": "Email sudah terdaftar" }
        """
        err = _require_admin()
        if err:
            return err

        try:
            name = (name or '').strip()
            email = (email or '').strip()
            phone = (phone or '').strip()
            password = password or ''

            # Validation
            if not all([name, email, phone, password]):
                return _error(400, 'Semua field wajib diisi: name, email, phone, password.')

            # Duplicate email check
            existing = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing:
                return _error(409, 'Email sudah terdaftar')

            # Create Odoo user
            user = request.env['res.users'].sudo().create({
                'name': name,
                'login': email,
                'email': email,
                'password': password,   # Odoo ORM hashes automatically
                'dke_role': 'customer_care',
                'dke_status': 'active',
                'dke_phone': phone,
                'groups_id': [(6, 0, [
                    request.env.ref('base.group_user').id,
                    request.env.ref('dke_crm.group_customer_care').id,
                ])],
            })

            return {
                'status': 'success',
                'code': 201,
                'data': {
                    'user_id': user.id,
                    'name': user.name,
                    'email': user.login,
                    'role': user.dke_role,
                },
            }

        except Exception as exc:
            _logger.exception('Create customer care error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  GET /api/accounts/customer-care  (PBI-4)                            #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/customer-care',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_customer_care(self, page=1, limit=20, search='', status='', **kwargs):
        """List Customer Care accounts with pagination, search, and filter.

        Query Params:
            page (default 1), limit (default 20), search, status

        Response 200:
            { "status": "success", "data": [...], "total": N, "page": 1, "limit": 20, "total_pages": M }
        """
        err = _require_admin()
        if err:
            return request.make_json_response(err)

        try:
            page = max(1, int(page))
            limit = min(100, max(1, int(limit)))
            search = (search or '').strip()
            status = (status or '').strip()

            domain = [('dke_role', '=', 'customer_care')]
            if search:
                domain += ['|', ('name', 'ilike', search), ('login', 'ilike', search)]
            if status in ('active', 'inactive'):
                domain.append(('dke_status', '=', status))

            Users = request.env['res.users'].sudo()
            total = Users.search_count(domain)
            users = Users.search(domain, offset=(page - 1) * limit, limit=limit, order='create_date desc')

            return request.make_json_response({
                'status': 'success',
                'data': [_serialize_user(u) for u in users],
                'total': total,
                'page': page,
                'limit': limit,
                'total_pages': math.ceil(total / limit) if total else 1,
            })

        except Exception as exc:
            _logger.exception('List customer care error')
            return request.make_json_response(_error(500, str(exc)))

    # ================================================================== #
    #  EXPERT STAFF                                                        #
    # ================================================================== #

    # ------------------------------------------------------------------ #
    #  POST /api/accounts/expert-staff  (PBI-5)                            #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/expert-staff',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_expert_staff(self, name='', email='', phone='', password='', specialization='', **kwargs):
        """Create a new Expert Staff account.

        JSON-RPC params:
            { "name": "...", "email": "...", "phone": "...", "password": "...", "specialization": "teknis|produk|pengiriman" }

        Response 201:
            { "status": "success", "data": { user_id, name, email, role, specialization } }
        """
        err = _require_admin()
        if err:
            return err

        try:
            name = (name or '').strip()
            email = (email or '').strip()
            phone = (phone or '').strip()
            password = password or ''
            specialization = (specialization or '').strip().lower()

            # Validation
            if not all([name, email, phone, password, specialization]):
                return _error(400, 'Semua field wajib diisi: name, email, phone, password, specialization.')

            if specialization not in VALID_SPECIALIZATIONS:
                return _error(400, f'Specialization tidak valid. Pilih dari: {", ".join(VALID_SPECIALIZATIONS)}.')

            # Duplicate email check
            existing = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing:
                return _error(409, 'Email sudah terdaftar')

            user = request.env['res.users'].sudo().create({
                'name': name,
                'login': email,
                'email': email,
                'password': password,
                'dke_role': 'expert_staff',
                'dke_status': 'active',
                'dke_phone': phone,
                'dke_specialization': specialization,
                'groups_id': [(6, 0, [
                    request.env.ref('base.group_user').id,
                    request.env.ref('dke_crm.group_expert_staff').id,
                ])],
            })

            return {
                'status': 'success',
                'code': 201,
                'data': {
                    'user_id': user.id,
                    'name': user.name,
                    'email': user.login,
                    'role': user.dke_role,
                    'specialization': user.dke_specialization,
                },
            }

        except Exception as exc:
            _logger.exception('Create expert staff error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  GET /api/accounts/expert-staff  (PBI-6)                             #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/expert-staff',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_expert_staff(self, page=1, limit=20, search='', status='', specialization='', **kwargs):
        """List Expert Staff accounts with pagination, search, and filter.

        Query Params:
            page, limit, search, specialization, status
        """
        err = _require_admin()
        if err:
            return request.make_json_response(err)

        try:
            page = max(1, int(page))
            limit = min(100, max(1, int(limit)))
            search = (search or '').strip()
            status = (status or '').strip()
            specialization = (specialization or '').strip().lower()

            domain = [('dke_role', '=', 'expert_staff')]
            if search:
                domain += ['|', ('name', 'ilike', search), ('login', 'ilike', search)]
            if status in ('active', 'inactive'):
                domain.append(('dke_status', '=', status))
            if specialization in VALID_SPECIALIZATIONS:
                domain.append(('dke_specialization', '=', specialization))

            Users = request.env['res.users'].sudo()
            total = Users.search_count(domain)
            users = Users.search(domain, offset=(page - 1) * limit, limit=limit, order='create_date desc')

            return request.make_json_response({
                'status': 'success',
                'data': [_serialize_user(u) for u in users],
                'total': total,
                'page': page,
                'limit': limit,
                'total_pages': math.ceil(total / limit) if total else 1,
            })

        except Exception as exc:
            _logger.exception('List expert staff error')
            return request.make_json_response(_error(500, str(exc)))

    # ------------------------------------------------------------------ #
    #  GET /api/accounts/<role>/<int:user_id> — get single user           #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/<string:role>/<int:user_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def get_user(self, role, user_id, **kwargs):
        """Return a single user by role and id."""
        err = _require_admin()
        if err:
            return request.make_json_response(err)
        try:
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists() or user.dke_role != role.replace('-', '_'):
                return request.make_json_response(_error(404, 'User tidak ditemukan.'))
            return request.make_json_response({'status': 'success', 'data': _serialize_user(user)})
        except Exception as exc:
            _logger.exception('Get user error')
            return request.make_json_response(_error(500, str(exc)))

    # ------------------------------------------------------------------ #
    #  POST /api/accounts/<role>/<int:user_id>/update — update user       #
    # (Odoo type='json' only supports POST, not PUT)                      #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/<string:role>/<int:user_id>/update',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def update_user(self, role, user_id,
                    name='', phone='', password='', specialization='', **kwargs):
        """Update an existing user's profile.

        JSON-RPC params:
            name, phone, password (optional), specialization (expert_staff only)
        """
        err = _require_admin()
        if err:
            return err
        try:
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists() or user.dke_role != role.replace('-', '_'):
                return _error(404, 'User tidak ditemukan.')

            name = (name or '').strip()
            phone = (phone or '').strip()
            password = (password or '').strip()
            specialization = (specialization or '').strip().lower()

            if not name:
                return _error(400, 'Nama tidak boleh kosong.')

            vals = {'name': name, 'dke_phone': phone}

            if password:
                if len(password) < 8:
                    return _error(400, 'Password minimal 8 karakter.')
                vals['password'] = password

            if role.replace('-', '_') == 'expert_staff':
                if specialization and specialization not in VALID_SPECIALIZATIONS:
                    return _error(400, f'Specialization tidak valid. Pilih dari: {", ".join(VALID_SPECIALIZATIONS)}.')
                if specialization:
                    vals['dke_specialization'] = specialization

            user.write(vals)
            return {'status': 'success', 'data': _serialize_user(user)}
        except Exception as exc:
            _logger.exception('Update user error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  POST /api/accounts/<role>/<int:user_id>/status — toggle active      #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/<string:role>/<int:user_id>/status',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def toggle_status(self, role, user_id, status='inactive', **kwargs):
        """Toggle a user's dke_status between active and inactive."""
        err = _require_admin()
        if err:
            return err

        try:
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists():
                return _error(404, 'User tidak ditemukan.')

            new_status = status or 'inactive'
            if new_status not in ('active', 'inactive'):
                return _error(400, 'Status tidak valid. Gunakan active atau inactive.')

            user.write({'dke_status': new_status})
            return {'status': 'success', 'data': _serialize_user(user)}

        except Exception as exc:
            _logger.exception('Toggle status error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  POST /api/accounts/<role>/<int:user_id>/delete — soft delete        #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/accounts/<string:role>/<int:user_id>/delete',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def soft_delete(self, role, user_id, **kwargs):
        """Soft-delete a user by setting active=False."""
        err = _require_admin()
        if err:
            return err

        try:
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists() or user.dke_role != role.replace('-', '_'):
                return _error(404, 'User tidak ditemukan.')

            user.write({'active': False, 'dke_status': 'inactive'})
            return {'status': 'success', 'message': f'Akun {user.name} berhasil dihapus.'}

        except Exception as exc:
            _logger.exception('Soft delete error')
            return _error(500, str(exc))
