# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PerformanceRecord(models.Model):
    """Periodic performance snapshot for staff evaluation.

    Stores aggregated metrics per user per period for historical tracking,
    trend analysis, and leaderboard ranking.
    """
    _name = 'dke.performance.record'
    _description = 'Performance Record'
    _order = 'period_end desc, weighted_score desc'

    user_id = fields.Many2one('res.users', string='Staff', required=True, ondelete='cascade')
    role = fields.Selection([
        ('expert_staff', 'Expert Staff'),
        ('customer_care', 'Customer Care'),
    ], string='Role', required=True)

    # Period
    period_start = fields.Date(string='Period Start', required=True)
    period_end = fields.Date(string='Period End', required=True)

    # Core metrics
    avg_rating = fields.Float(string='Avg Rating (1-5)')
    sla_compliance_rate = fields.Float(
        string='SLA Compliance Rate (%)',
        help='Percentage of tickets/chats handled within SLA time limits.'
    )
    avg_response_minutes = fields.Float(string='Avg Response Time (min)')
    avg_resolution_minutes = fields.Float(
        string='Avg Resolution Time (min)',
        help='Expert staff only — average minutes from assignment to resolution.'
    )

    # Volume
    total_tickets_handled = fields.Integer(string='Tickets Handled')
    total_chats_handled = fields.Integer(string='Chats Handled')
    total_ratings_received = fields.Integer(string='Ratings Received')

    # Composite score
    weighted_score = fields.Float(
        string='Weighted Score',
        help='Composite performance score (0-100) based on weighted metrics.'
    )

    @api.model
    def compute_weighted_score(self, role, avg_rating, sla_compliance, avg_response_minutes, avg_resolution_minutes=0):
        """Compute weighted performance score (0-100).

        Expert Staff:  30% rating + 25% SLA + 25% response_time + 20% resolution_time
        Customer Care: 35% rating + 30% SLA + 35% response_time
        """
        # Normalize rating: (rating / 5) * 100
        norm_rating = (avg_rating / 5.0) * 100 if avg_rating else 0

        # SLA compliance already in % (0-100)
        norm_sla = min(sla_compliance, 100) if sla_compliance else 0

        # Normalize response time: lower is better
        # Use 60 min as "worst acceptable" baseline for normalization
        norm_response = max(0, (1 - (avg_response_minutes / 60.0))) * 100 if avg_response_minutes else 100

        if role == 'expert_staff':
            # Normalize resolution: use 1440 min (24h) as worst baseline
            norm_resolution = max(0, (1 - (avg_resolution_minutes / 1440.0))) * 100 if avg_resolution_minutes else 100
            score = (0.30 * norm_rating) + (0.25 * norm_sla) + (0.25 * norm_response) + (0.20 * norm_resolution)
        else:
            score = (0.35 * norm_rating) + (0.30 * norm_sla) + (0.35 * norm_response)

        return round(max(0, min(100, score)), 2)
