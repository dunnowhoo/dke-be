# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class TicketingSession(models.Model):
    """Ticketing Session lifecycle tracking.

    Tracks a single interaction session within a Ticketing Room,
    including which CS/Expert handled it, ratings, and timing.

    EPIC01 - PBI-6
    """
    _name = 'dke.ticketing.session'
    _description = 'Ticketing Session'
    _order = 'started_at desc'

    session_code = fields.Char(
        string='Session Code', required=True, copy=False, readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('dke.ticketing.session') or 'NEW'
    )
    room_id = fields.Many2one(
        'dke.ticketing.room', string='Ticketing Room',
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

    # CS → Expert rating (filled by Customer Care when closing ticket)
    cs_expert_rating = fields.Selection([
        ('1', '1 - Very Poor'),
        ('2', '2 - Poor'),
        ('3', '3 - Average'),
        ('4', '4 - Good'),
        ('5', '5 - Excellent'),
    ], string='CS Expert Rating',
        help='Rating given by Customer Care for Expert Staff performance on this ticket.')
    cs_expert_feedback = fields.Text(
        string='CS Expert Feedback',
        help='Optional feedback from CS about expert performance.')
    expert_avg_response_minutes = fields.Float(
        string='Expert Avg Response (min)',
        help='Average minutes expert took to respond to CS messages in this session.')

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

    def _compute_expert_response_time(self):
        """Compute average expert response time from message timestamps.

        Looks at pairs: CS message → next Expert message in the same room.
        Returns average minutes between them.
        """
        for rec in self:
            if not rec.room_id or not rec.expert_user_id:
                rec.expert_avg_response_minutes = 0
                continue

            messages = self.env['dke.ticketing.message'].sudo().search([
                ('room_id', '=', rec.room_id.id),
            ], order='created_at asc')

            response_times = []
            last_cs_time = None

            for msg in messages:
                if msg.sender_type == 'cs':
                    last_cs_time = msg.created_at
                elif msg.sender_type in ('cs', 'ai'):
                    # Expert replies come as 'cs' sender_type from expert user
                    # Check if sender is the expert
                    pass

            # Better approach: look at message sender_id
            last_cs_time = None
            response_times = []
            for msg in messages:
                if msg.sender_type == 'cs' and msg.sender_id and msg.sender_id.id != rec.expert_user_id.id:
                    # Message from CS (not expert)
                    last_cs_time = msg.created_at
                elif msg.sender_type == 'cs' and msg.sender_id and msg.sender_id.id == rec.expert_user_id.id:
                    # Reply from expert
                    if last_cs_time and msg.created_at:
                        diff = (msg.created_at - last_cs_time).total_seconds() / 60.0
                        if diff > 0:
                            response_times.append(diff)
                    last_cs_time = None

            if response_times:
                rec.expert_avg_response_minutes = round(sum(response_times) / len(response_times), 2)
            else:
                rec.expert_avg_response_minutes = 0

    def action_close(self):
        """Close the session and record the end time."""
        for rec in self:
            rec._compute_expert_response_time()
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
            # Set escalated_at on linked ticket
            if rec.room_id:
                tickets = self.env['helpdesk.ticket'].sudo().search([
                    ('channel_id', '=', rec.room_id.id),
                ], limit=1)
                if tickets and not tickets.escalated_at:
                    tickets.write({'escalated_at': fields.Datetime.now()})
