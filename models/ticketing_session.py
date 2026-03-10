# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class TicketingSession(models.Model):
    """Chat session lifecycle tracking.

    Tracks a single interaction session within a chat room,
    including which CS/Expert handled it, ratings, and timing.

    EPIC01 - PBI-6
    """
    _name = 'dke.ticketing.session'
    _description = 'Chat Session'
    _order = 'started_at desc'

    session_code = fields.Char(
        string='Session Code', required=True, copy=False, readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('dke.ticketing.session') or 'NEW'
    )
    room_id = fields.Many2one(
        'dke.ticketing.room', string='Chat Room',
        required=True, ondelete='cascade'
    )

    # Staff assignment
    cs_user_id = fields.Many2one(
        'res.users', string='Customer Care',
        help='The Customer Care agent handling this session'
    )
    expert_user_id = fields.Many2one(
        'res.users', string='Expert Staff',
        help='Expert staff assigned for escalation'
    )

    # Customer feedback
    customer_rating = fields.Selection([
        ('1', '1 - Very Poor'),
        ('2', '2 - Poor'),
        ('3', '3 - Average'),
        ('4', '4 - Good'),
        ('5', '5 - Excellent'),
    ], string='Customer Rating')
    customer_feedback = fields.Text(string='Customer Feedback')

    # State
    state = fields.Selection([
        ('active', 'Active'),
        ('escalated', 'Escalated'),
        ('closed', 'Closed'),
    ], string='Status', default='active')

    # Timestamps
    started_at = fields.Datetime(
        string='Started At', default=fields.Datetime.now
    )
    ended_at = fields.Datetime(string='Ended At')

    # Metrics (for business process parameters)
    first_response_seconds = fields.Integer(string='First Response Time (sec)')
    total_messages = fields.Integer(string='Total Messages in Session')

    def action_close(self):
        """Close the session and record the end time."""
        for rec in self:
            rec.write({
                'state': 'closed',
                'ended_at': fields.Datetime.now(),
            })

    def action_escalate(self, expert_user_id=None):
        """Escalate the session to an expert staff."""
        for rec in self:
            vals = {'state': 'escalated'}
            if expert_user_id:
                vals['expert_user_id'] = expert_user_id
            rec.write(vals)
