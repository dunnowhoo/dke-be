# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResUsers(models.Model):
    """Extend res.users with DKE CRM role fields and expert performance metrics."""
    _inherit = 'res.users'

    dke_role = fields.Selection([
        ('customer_care', 'Customer Care'),
        ('sales_staff', 'Sales Staff'),
        ('sales_manager', 'Sales Manager'),
        ('expert_staff', 'Expert Staff'),
    ], string='DKE Role')

    dke_status = fields.Selection([
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ], string='DKE Status', default='active')

    dke_specialization = fields.Selection([
        ('face_wash', 'Face Wash'),
        ('serum', 'Serum'),
        ('lotion', 'Lotion'),
        ('toner', 'Toner'),
    ], string='Specialization')

    dke_phone = fields.Char(string='DKE Phone')

    # ------------------------------------------------------------------ #
    # Performance stats — stored, refreshed by _recompute_expert_stats()  #
    # ------------------------------------------------------------------ #
    avg_response_time = fields.Float(string='Avg Response Time (min)')
    avg_resolution_time = fields.Float(
        string='Avg Resolution Time (hours)',
        help='Average hours from ticket creation to resolution for this expert.',
    )
    avg_rating = fields.Float(string='Avg Customer Rating')
    total_chats_handled = fields.Integer(string='Total Chats Handled')
    total_tickets_resolved = fields.Integer(
        string='Total Tickets Resolved',
        help='Number of tickets with state resolved/closed assigned to this expert.',
    )
    total_messages_sent = fields.Integer(string='Total Messages Sent')
    avg_resolution_message_count = fields.Float(
        string='Avg Messages per Resolution',
        help='Average number of chat messages from ticket open to close.',
    )

    # ------------------------------------------------------------------ #
    # Recompute helper — called after ticket resolve/close                 #
    # ------------------------------------------------------------------ #

    def _recompute_expert_stats(self):
        """Recompute stored performance stats for this expert user."""
        Ticket = self.env['dke.support.ticket'].sudo()
        for user in self:
            resolved_tickets = Ticket.search([
                ('assigned_expert_id', '=', user.id),
                ('state', 'in', ('resolved', 'closed')),
            ])

            count = len(resolved_tickets)
            if count:
                avg_hours = sum(t.resolution_time_hours for t in resolved_tickets) / count
                avg_msgs = sum(t.message_count for t in resolved_tickets) / count
                # Rating from sessions linked to the rooms of these tickets
                ratings = []
                for t in resolved_tickets:
                    if t.room_id:
                        for s in t.room_id.session_ids:
                            if s.customer_rating:
                                ratings.append(int(s.customer_rating))
                avg_rat = sum(ratings) / len(ratings) if ratings else 0.0
            else:
                avg_hours = 0.0
                avg_msgs = 0.0
                avg_rat = 0.0

            user.write({
                'total_tickets_resolved': count,
                'avg_resolution_time': round(avg_hours, 2),
                'avg_resolution_message_count': round(avg_msgs, 2),
                'avg_rating': round(avg_rat, 2),
            })
