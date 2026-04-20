# -*- coding: utf-8 -*-

import logging
import re

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

VALID_TARGET_ROLES = ('all', 'customer_care', 'expert_staff')
VALID_PRIORITIES = ('normal', 'urgent')
CREATOR_ROLES = ('admin', 'sales_manager')


def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def _strip_html(html_str):
    return re.sub(r'<[^>]+>', '', html_str or '').strip()


def _serialize_list_item(rec, is_read=False):
    return {
        'announcement_id': rec.id,
        'title': rec.title or '',
        'excerpt': _strip_html(rec.content)[:180] + ('...' if len(_strip_html(rec.content)) > 180 else ''),
        'priority': rec.priority or 'normal',
        'created_at': rec.created_at.isoformat() if rec.created_at else None,
        'is_read': bool(is_read),
        'expiry_date': rec.expiry_date.isoformat() if rec.expiry_date else None,
    }


class AnnouncementController(http.Controller):
    """Announcement API endpoints."""

    @http.route(
        '/api/announcements',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_announcement(
        self,
        title='',
        content='',
        target_role='all',
        priority='normal',
        expiry_date='',
        **kwargs,
    ):
        """POST /api/announcements - Create announcement."""
        try:
            user = request.env.user
            role = user.dke_role or ('admin' if user._is_admin() else None)
            if role not in CREATOR_ROLES:
                return _error(403, 'Hanya Admin atau Sales Manager yang dapat membuat pengumuman.')

            title = (title or '').strip()
            content = (content or '').strip()
            target_role = (target_role or 'all').strip()
            priority = (priority or 'normal').strip()

            if not title:
                return _error(400, 'Judul wajib diisi.')
            if not content:
                return _error(400, 'Konten wajib diisi.')
            if target_role not in VALID_TARGET_ROLES:
                return _error(400, 'Target role tidak valid.')
            if priority not in VALID_PRIORITIES:
                return _error(400, 'Prioritas tidak valid.')

            parsed_expiry = False
            if expiry_date:
                try:
                    parsed_expiry = fields.Date.from_string(expiry_date)
                except Exception:
                    return _error(400, 'Format expiry_date tidak valid. Gunakan YYYY-MM-DD.')

            rec = request.env['dke.announcement'].sudo().create({
                'title': title,
                'content': content,
                'target_role': target_role,
                'priority': priority,
                'expiry_date': parsed_expiry,
                'created_by': user.id,
                'created_at': fields.Datetime.now(),
            })

            return {
                'status': 'success',
                'data': {
                    'announcement_id': rec.id,
                    'title': rec.title,
                    'priority': rec.priority,
                    'target_role': rec.target_role,
                    'created_by': rec.created_by.id,
                    'created_at': rec.created_at.isoformat() if rec.created_at else None,
                    'expiry_date': rec.expiry_date.isoformat() if rec.expiry_date else None,
                },
            }
        except Exception as exc:
            _logger.exception('Create announcement error')
            return _error(500, str(exc))

    @http.route(
        '/api/announcements',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def list_announcements(self, **kwargs):
        """GET /api/announcements?role={user_role} - List announcements."""
        try:
            user = request.env.user
            requested_role = (kwargs.get('role') or '').strip()
            user_role = user.dke_role or ('admin' if user._is_admin() else '')
            role = requested_role or user_role

            today = fields.Date.context_today(user)
            domain = [
                '|',
                ('expiry_date', '=', False),
                ('expiry_date', '>=', today),
            ]

            role_targets = ['all']
            if role in ('admin', 'sales_manager'):
                role_targets = ['all', 'customer_care', 'expert_staff']
            elif role in ('customer_care', 'expert_staff'):
                role_targets.append(role)

            domain += [('target_role', 'in', role_targets)]

            records = request.env['dke.announcement'].sudo().search(domain, order='priority desc, created_at desc')

            read_rows = request.env['dke.announcement.read'].sudo().search([
                ('user_id', '=', user.id),
                ('announcement_id', 'in', records.ids),
            ])
            read_map = {row.announcement_id.id: True for row in read_rows}

            data = [_serialize_list_item(rec, read_map.get(rec.id, False)) for rec in records]
            return request.make_json_response(data)
        except Exception as exc:
            _logger.exception('List announcements error')
            return request.make_json_response({'status': 'error', 'message': str(exc)}, status=500)

    @http.route(
        '/api/announcements/<int:announcement_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        cors='*',
    )
    def announcement_detail(self, announcement_id, **kwargs):
        """GET /api/announcements/<id> - Detail + mark as read for current user."""
        try:
            user = request.env.user
            rec = request.env['dke.announcement'].sudo().browse(announcement_id)
            if not rec.exists():
                return request.make_json_response({'status': 'error', 'message': 'Pengumuman tidak ditemukan.'}, status=404)

            today = fields.Date.context_today(user)
            if rec.expiry_date and rec.expiry_date < today:
                return request.make_json_response({'status': 'error', 'message': 'Pengumuman sudah kedaluwarsa.'}, status=404)

            role = user.dke_role or ('admin' if user._is_admin() else '')
            if role in ('admin', 'sales_manager'):
                allowed_roles = ['all', 'customer_care', 'expert_staff']
            else:
                allowed_roles = ['all']
                if role in ('customer_care', 'expert_staff'):
                    allowed_roles.append(role)
            if rec.target_role not in allowed_roles:
                return request.make_json_response({'status': 'error', 'message': 'Anda tidak memiliki akses.'}, status=403)

            read_model = request.env['dke.announcement.read'].sudo()
            has_read = read_model.search_count([
                ('announcement_id', '=', rec.id),
                ('user_id', '=', user.id),
            ]) > 0
            if not has_read:
                read_model.create({
                    'announcement_id': rec.id,
                    'user_id': user.id,
                    'read_at': fields.Datetime.now(),
                })

            return request.make_json_response({
                'announcement_id': rec.id,
                'title': rec.title or '',
                'content': rec.content or '',
                'target_role': rec.target_role,
                'priority': rec.priority,
                'created_at': rec.created_at.isoformat() if rec.created_at else None,
                'expiry_date': rec.expiry_date.isoformat() if rec.expiry_date else None,
                'created_by': {
                    'id': rec.created_by.id,
                    'name': rec.created_by.name or '',
                },
                'is_read': True,
            })
        except Exception as exc:
            _logger.exception('Announcement detail error')
            return request.make_json_response({'status': 'error', 'message': str(exc)}, status=500)

    @http.route(
        '/api/announcements/<int:announcement_id>',
        type='http',
        auth='user',
        methods=['DELETE'],
        csrf=False,
        cors='*',
    )
    def delete_announcement(self, announcement_id, **kwargs):
        """DELETE /api/announcements/<id> - Delete announcement."""
        try:
            user = request.env.user
            role = user.dke_role or ('admin' if user._is_admin() else None)
            if role not in CREATOR_ROLES:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya Admin atau Sales Manager yang dapat menghapus pengumuman.'},
                    status=403,
                )

            rec = request.env['dke.announcement'].sudo().browse(announcement_id)
            if not rec.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Pengumuman tidak ditemukan.'},
                    status=404,
                )

            rec.unlink()
            return request.make_json_response({'status': 'success', 'message': 'Pengumuman berhasil dihapus.'})
        except Exception as exc:
            _logger.exception('Delete announcement error')
            return request.make_json_response({'status': 'error', 'message': str(exc)}, status=500)
