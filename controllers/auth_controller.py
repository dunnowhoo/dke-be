# -*- coding: utf-8 -*-

import json
import logging
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class AuthController(http.Controller):
    """REST API endpoints for Authentication (PBI-1 & PBI-2)."""

    # ------------------------------------------------------------------ #
    #  POST /api/auth/login  (PBI-1)                                       #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/auth/login',
        type='json',
        auth='none',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def login(self, email='', password='', **kwargs):
        """Authenticate a user and return session info with DKE role.

        JSON-RPC params: { "email": "...", "password": "..." }

        Response success:
            { status, access_token, user_id, name, email, dke_role, dke_status }
        Response 401:
            { status, code, message }
        """
        try:
            email = (email or '').strip()
            password = password or ''

            if not email or not password:
                return _error(400, 'Email dan password wajib diisi.')

            db = request.db

            # authenticate() validates credentials AND opens the session
            try:
                uid = request.session.authenticate(db, email, password)
            except AccessDenied:
                uid = False

            if not uid:
                return _error(401, 'Email atau Password salah')

            user = request.env['res.users'].sudo().browse(uid)

            # Determine role string
            dke_role = user.dke_role or ('admin' if user._is_admin() else None)

            return {
                'status': 'success',
                'access_token': request.session.sid,
                'user_id': uid,
                'name': user.name,
                'email': user.login,
                'dke_role': dke_role,
                'dke_status': user.dke_status or 'active',
            }

        except Exception as exc:
            _logger.exception('Login error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  POST /api/auth/logout  (PBI-2)                                      #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/auth/logout',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def logout(self, **kwargs):
        """Invalidate the current session.

        Response 200:
            { "status": "success", "message": "Logout berhasil" }

        Response 401:
            { "status": "error", "message": "Unauthorized" }
        """
        try:
            request.session.logout(keep_db=True)
            return {'status': 'success', 'message': 'Logout berhasil'}
        except Exception as exc:
            _logger.exception('Logout error')
            return _error(500, str(exc))

    # ------------------------------------------------------------------ #
    #  GET /api/auth/me — return current user info                         #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/auth/me',
        type='json',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def me(self, **kwargs):
        """Return current authenticated user's profile."""
        user = request.env.user
        dke_role = user.dke_role or ('admin' if user._is_admin() else None)
        return {
            'status': 'success',
            'data': {
                'user_id': user.id,
                'name': user.name,
                'email': user.login,
                'dke_role': dke_role,
                'dke_status': user.dke_status or 'active',
            },
        }


# ------------------------------------------------------------------ #
#  Helpers                                                              #
# ------------------------------------------------------------------ #
def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}
