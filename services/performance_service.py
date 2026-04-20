# -*- coding: utf-8 -*-

from datetime import date, timedelta
from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


class PerformanceService:
    """Service for computing and storing periodic performance records.

    Called by cron job or manually to generate performance snapshots.
    """

    @staticmethod
    def generate_periodic_records(env, period_start=None, period_end=None):
        """Generate performance records for all active staff for given period.

        If no period specified, defaults to last 30 days.
        """
        if not period_start:
            period_end = date.today()
            period_start = period_end - timedelta(days=30)

        Users = env['res.users'].sudo()
        Record = env['dke.performance.record'].sudo()

        # Expert Staff
        experts = Users.search([('dke_role', '=', 'expert_staff'), ('dke_status', '=', 'active')])
        for expert in experts:
            expert._recompute_expert_stats()
            score = Record.compute_weighted_score(
                'expert_staff',
                expert.expert_avg_cs_rating,
                expert.expert_sla_compliance_rate,
                expert.expert_avg_response_minutes,
                expert.avg_resolution_time * 60,  # hours → minutes
            )
            # Check if record exists for this period
            existing = Record.search([
                ('user_id', '=', expert.id),
                ('period_start', '=', period_start),
                ('period_end', '=', period_end),
                ('role', '=', 'expert_staff'),
            ], limit=1)
            vals = {
                'user_id': expert.id,
                'role': 'expert_staff',
                'period_start': period_start,
                'period_end': period_end,
                'avg_rating': expert.expert_avg_cs_rating,
                'sla_compliance_rate': expert.expert_sla_compliance_rate,
                'avg_response_minutes': expert.expert_avg_response_minutes,
                'avg_resolution_minutes': expert.avg_resolution_time * 60,
                'total_tickets_handled': expert.total_tickets_resolved,
                'total_ratings_received': 0,  # computed below
                'weighted_score': score,
            }
            if existing:
                existing.write(vals)
            else:
                Record.create(vals)

        # Customer Care
        cares = Users.search([('dke_role', '=', 'customer_care'), ('dke_status', '=', 'active')])
        for care in cares:
            care._recompute_care_stats()
            score = Record.compute_weighted_score(
                'customer_care',
                care.care_avg_rating,
                care.care_sla_compliance_rate,
                care.care_avg_response_minutes,
            )
            existing = Record.search([
                ('user_id', '=', care.id),
                ('period_start', '=', period_start),
                ('period_end', '=', period_end),
                ('role', '=', 'customer_care'),
            ], limit=1)
            vals = {
                'user_id': care.id,
                'role': 'customer_care',
                'period_start': period_start,
                'period_end': period_end,
                'avg_rating': care.care_avg_rating,
                'sla_compliance_rate': care.care_sla_compliance_rate,
                'avg_response_minutes': care.care_avg_response_minutes,
                'avg_resolution_minutes': 0,
                'total_chats_handled': care.total_chats_handled,
                'total_ratings_received': care.care_total_chats_rated,
                'weighted_score': score,
            }
            if existing:
                existing.write(vals)
            else:
                Record.create(vals)

        _logger.info(
            'Performance records generated: %d experts, %d care agents, period %s to %s',
            len(experts), len(cares), period_start, period_end
        )

    @staticmethod
    def get_leaderboard(env, role, period_start=None, period_end=None, limit=20):
        """Get top performers for a given role and period."""
        Record = env['dke.performance.record'].sudo()
        domain = [('role', '=', role)]
        if period_start:
            domain.append(('period_start', '>=', period_start))
        if period_end:
            domain.append(('period_end', '<=', period_end))

        records = Record.search(domain, order='weighted_score desc', limit=limit)
        return [{
            'user_id': r.user_id.id,
            'user_name': r.user_id.name,
            'avg_rating': r.avg_rating,
            'sla_compliance_rate': r.sla_compliance_rate,
            'avg_response_minutes': r.avg_response_minutes,
            'avg_resolution_minutes': r.avg_resolution_minutes,
            'total_tickets_handled': r.total_tickets_handled,
            'total_chats_handled': r.total_chats_handled,
            'weighted_score': r.weighted_score,
            'period_start': str(r.period_start),
            'period_end': str(r.period_end),
        } for r in records]
