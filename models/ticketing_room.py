# -*- coding: utf-8 -*-

from odoo import models, fields, api


class TicketingRoom(models.Model):
    """Ticketing Room / conversation thread.

    Represents a conversation between Customer Care and a customer,
    sourced from marketplace (Shopee) or WhatsApp.

    EPIC01 - PBI-1, PBI-2, PBI-6
    """
    _name = 'dke.ticketing.room'
    _description = 'Ticketing Room'
    _order = 'last_message_time desc'

    name = fields.Char(string='Room Name', required=True)
    customer_name = fields.Char(string='Customer Name')
    customer_phone = fields.Char(string='Customer Phone / ID')
    customer_initial = fields.Char(
        string='Customer Initial', compute='_compute_initial', store=True
    )
    customer_id = fields.Many2one('res.partner', string='Customer')

    # Source / Channel
    source = fields.Selection([
        ('shopee', 'Shopee'),
        ('whatsapp', 'WhatsApp'),
        ('platform', 'Platform'),
    ], string='Source Channel', default='shopee')
    external_conversation_id = fields.Char(string='External Conversation ID')

    # Status
    state = fields.Selection([
        ('active', 'Active'),
        ('done', 'Done'),
        ('archived', 'Archived'),
    ], string='Status', default='active')

    # Timestamps
    last_message_time = fields.Datetime(string='Last Message Time')
    last_sync_time = fields.Datetime(string='Last Sync Time')

    # Counters
    unread_count = fields.Integer(string='Unread Count', default=0)

    # Claim / Assignment (PBI-7, PBI-9)
    is_assigned = fields.Boolean(
        string='Is Assigned',
        default=False,
        help='True jika chat sudah diklaim oleh Customer Care.',
    )
    assigned_to = fields.Many2one(
        'res.users',
        string='Assigned To',
        help='Customer Care yang sedang menangani chat ini.',
    )
    assigned_at = fields.Datetime(string='Assigned At')

    # Relations
    message_ids = fields.One2many('dke.ticketing.message', 'room_id', string='Messages')
    session_ids = fields.One2many('dke.ticketing.session', 'room_id', string='Sessions')
    ticket_ids = fields.One2many('dke.support.ticket', 'room_id', string='Support Tickets')
    scheduled_message_ids = fields.One2many(
        'dke.scheduled.message', 'room_id', string='Scheduled Messages'
    )

    # 1-to-1: the primary ticket linked to this chat bubble
    ticket_id = fields.Many2one(
        'dke.support.ticket',
        string='Linked Ticket',
        compute='_compute_ticket_id',
        store=False,
    )
    ticket_subject = fields.Char(
        string='Ticket Subject',
        compute='_compute_ticket_id',
        store=False,
        help='Subject of the primary linked ticket (= chat bubble title).',
    )
    ticket_priority = fields.Char(
        string='Ticket Priority',
        compute='_compute_ticket_id',
        store=False,
    )
    ticket_state = fields.Char(
        string='Ticket Status',
        compute='_compute_ticket_id',
        store=False,
    )

    @api.depends('ticket_ids', 'ticket_ids.subject', 'ticket_ids.priority', 'ticket_ids.state')
    def _compute_ticket_id(self):
        for rec in self:
            ticket = rec.ticket_ids[:1]
            rec.ticket_id = ticket
            rec.ticket_subject = ticket.subject if ticket else ''
            rec.ticket_priority = ticket.priority if ticket else ''
            rec.ticket_state = ticket.state if ticket else ''

    @api.depends('customer_name')
    def _compute_initial(self):
        for rec in self:
            if rec.customer_name:
                parts = rec.customer_name.strip().split()
                rec.customer_initial = ''.join([p[0].upper() for p in parts[:2]])
            else:
                rec.customer_initial = '--'

    def get_active_session(self):
        """Return the currently active session for this room, or False."""
        self.ensure_one()
        return self.session_ids.filtered(lambda s: s.state == 'active')[:1]

    def to_dict(self):
        """Serialize Ticketing Room to dictionary for API response."""
        self.ensure_one()
        active_session = self.get_active_session()
        assigned_name = ''
        if self.assigned_to:
            assigned_name = self.assigned_to.name or ''

        ticket = self.ticket_ids[:1]

        return {
            'id': self.id,
            # Display name = ticket subject (chat bubble title), fallback to room name
            'name': ticket.subject if ticket else self.name,
            'room_name': self.name,
            'customer_name': self.customer_name or '',
            'customer_phone': self.customer_phone or '',
            'customer_initial': self.customer_initial or '--',
            'platform': self.source or 'platform',
            'state': self.state,
            'assigned_cs': assigned_name,
            'assigned_cs_id': self.assigned_to.id if self.assigned_to else None,
            'last_message_time': self.last_message_time.isoformat() if self.last_message_time else None,
            'unread_count': self.unread_count,
            'session_id': active_session.id if active_session else None,
            'session_code': active_session.session_code if active_session else None,
            'customer_rating': active_session.customer_rating if active_session else None,
            # Ticket info
            'ticket_id': ticket.id if ticket else None,
            'ticket_subject': ticket.subject if ticket else '',
            'ticket_priority': ticket.priority if ticket else '',
            'ticket_state': ticket.state if ticket else '',
            'ticket_required_specialization': ticket.required_specialization if ticket else '',
            'ticket_topic': ticket.topic if ticket else '',
        }
