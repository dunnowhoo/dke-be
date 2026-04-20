# -*- coding: utf-8 -*-

from odoo import http, fields
from odoo.http import request
from datetime import date, timedelta
import json
import logging

_logger = logging.getLogger(__name__)


class PerformanceController(http.Controller):
    """REST API endpoints for Performance Evaluation Dashboard."""

    # ──────────────────────────────────────────────────────────────
    # Performance Overview
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/performance/overview', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def performance_overview(self, **kwargs):
        """GET /api/performance/overview — Get performance stats for current user or all staff (manager).

        Query params:
          - user_id: specific user (manager only)
          - role: 'expert_staff' or 'customer_care'
          - period_days: lookback period in days (default 30)
        """
        try:
            user = request.env.user
            role = getattr(user, 'dke_role', '')
            target_user_id = int(kwargs.get('user_id', 0)) or user.id
            filter_role = kwargs.get('role', '')
            period_days = int(kwargs.get('period_days', 30))

            # Only managers can view other users
            if target_user_id != user.id and role not in ('sales_manager',):
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            target = request.env['res.users'].sudo().browse(target_user_id)
            if not target.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'User tidak ditemukan.'}, status=404
                )

            target_role = filter_role or target.dke_role
            data = {}

            if target_role == 'expert_staff':
                target._recompute_expert_stats()
                data = {
                    'user_id': target.id,
                    'user_name': target.name,
                    'role': 'expert_staff',
                    'specialization': target.dke_specialization,
                    'metrics': {
                        'avg_cs_rating': target.expert_avg_cs_rating,
                        'avg_customer_rating': target.avg_rating,
                        'sla_compliance_rate': target.expert_sla_compliance_rate,
                        'avg_response_minutes': target.expert_avg_response_minutes,
                        'avg_resolution_hours': target.avg_resolution_time,
                        'total_tickets_resolved': target.total_tickets_resolved,
                        'avg_messages_per_resolution': target.avg_resolution_message_count,
                    },
                }
            elif target_role == 'customer_care':
                target._recompute_care_stats()
                data = {
                    'user_id': target.id,
                    'user_name': target.name,
                    'role': 'customer_care',
                    'metrics': {
                        'avg_customer_rating': target.care_avg_rating,
                        'sla_compliance_rate': target.care_sla_compliance_rate,
                        'avg_response_minutes': target.care_avg_response_minutes,
                        'total_chats_handled': target.total_chats_handled,
                        'total_chats_rated': target.care_total_chats_rated,
                    },
                }
            else:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Role tidak valid untuk performance.'}, status=400
                )

            return request.make_json_response({'status': 'success', 'data': data})
        except Exception as e:
            _logger.error("performance_overview error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Leaderboard
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/performance/leaderboard', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def leaderboard(self, **kwargs):
        """GET /api/performance/leaderboard — Get ranked staff by weighted score.

        Query params:
          - role: 'expert_staff' or 'customer_care' (required)
          - limit: max results (default 20)
        """
        try:
            user = request.env.user
            role = getattr(user, 'dke_role', '')
            if role not in ('sales_manager', 'customer_care', 'expert_staff'):
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            filter_role = kwargs.get('role', '')
            if filter_role not in ('expert_staff', 'customer_care'):
                return request.make_json_response(
                    {'status': 'error', 'message': 'Parameter role wajib (expert_staff/customer_care).'}, status=400
                )

            limit = int(kwargs.get('limit', 20))
            Record = request.env['dke.performance.record'].sudo()
            records = Record.search([('role', '=', filter_role)], order='weighted_score desc', limit=limit)

            data = [{
                'rank': idx + 1,
                'user_id': r.user_id.id,
                'user_name': r.user_id.name,
                'avg_rating': r.avg_rating,
                'sla_compliance_rate': r.sla_compliance_rate,
                'avg_response_minutes': r.avg_response_minutes,
                'avg_resolution_minutes': r.avg_resolution_minutes,
                'weighted_score': r.weighted_score,
                'period_start': str(r.period_start) if r.period_start else None,
                'period_end': str(r.period_end) if r.period_end else None,
            } for idx, r in enumerate(records)]

            return request.make_json_response({'status': 'success', 'data': data})
        except Exception as e:
            _logger.error("leaderboard error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Generate Performance Records (admin/cron trigger)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/performance/generate', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def generate_records(self, **kwargs):
        """POST /api/performance/generate — Trigger performance record generation.

        Body (JSON): { "period_days": 30 }
        Manager only.
        """
        try:
            user = request.env.user
            role = getattr(user, 'dke_role', '')
            if role != 'sales_manager' and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya Sales Manager yang dapat generate records.'}, status=403
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            period_days = int(body.get('period_days', 30))

            period_end = date.today()
            period_start = period_end - timedelta(days=period_days)

            from ..services.performance_service import PerformanceService
            PerformanceService.generate_periodic_records(request.env, period_start, period_end)

            return request.make_json_response({
                'status': 'success',
                'message': 'Performance records generated for %s to %s.' % (period_start, period_end),
            })
        except Exception as e:
            _logger.error("generate_records error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # SLA Policies (CRUD for manager)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/performance/sla-policies', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def list_sla_policies(self, **kwargs):
        """GET /api/performance/sla-policies — List all SLA policies."""
        try:
            policies = request.env['dke.sla.policy'].sudo().search([])
            data = [{
                'id': p.id,
                'name': p.name,
                'target_type': p.target_type,
                'priority': p.priority,
                'max_minutes': p.max_minutes,
                'applies_to': p.applies_to,
                'active': p.active,
            } for p in policies]
            return request.make_json_response({'status': 'success', 'data': data})
        except Exception as e:
            _logger.error("list_sla_policies error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/performance/sla-policies', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_sla_policy(self, **kwargs):
        """POST /api/performance/sla-policies — Create/update SLA policy (manager only)."""
        try:
            user = request.env.user
            if getattr(user, 'dke_role', '') != 'sales_manager' and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}

            required = ('name', 'target_type', 'max_minutes', 'applies_to')
            for field in required:
                if not body.get(field):
                    return request.make_json_response(
                        {'status': 'error', 'message': '%s wajib diisi.' % field}, status=400
                    )

            vals = {
                'name': body['name'],
                'target_type': body['target_type'],
                'priority': body.get('priority') or False,
                'max_minutes': int(body['max_minutes']),
                'applies_to': body['applies_to'],
                'active': body.get('active', True),
            }

            policy = request.env['dke.sla.policy'].sudo().create(vals)
            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': policy.id,
                    'name': policy.name,
                    'target_type': policy.target_type,
                    'priority': policy.priority,
                    'max_minutes': policy.max_minutes,
                    'applies_to': policy.applies_to,
                    'active': policy.active,
                },
            })
        except Exception as e:
            _logger.error("create_sla_policy error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )
