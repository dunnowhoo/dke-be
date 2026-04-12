# -*- coding: utf-8 -*-

import threading
from odoo import models, fields, api

# Anti-recursion guard: prevents room.create → ticket.create → room.create loop
_creating_ticket = threading.local()


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
    ticket_ids = fields.One2many('helpdesk.ticket', 'channel_id', string='Helpdesk Tickets')
    scheduled_message_ids = fields.One2many(
        'dke.scheduled.message', 'room_id', string='Scheduled Messages'
    )

    # 1-to-1: the primary ticket linked to this chat bubble
    ticket_id = fields.Many2one(
        'helpdesk.ticket',
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

    @api.depends('ticket_ids', 'ticket_ids.name', 'ticket_ids.priority', 'ticket_ids.stage_id')
    def _compute_ticket_id(self):
        for rec in self:
            ticket = rec.ticket_ids[:1]
            rec.ticket_id = ticket
            rec.ticket_subject = ticket.name if ticket else ''
            rec.ticket_priority = ticket.priority if ticket else ''
            rec.ticket_state = ticket.stage_id.name if ticket and ticket.stage_id else ''

    @api.depends('customer_name')
    def _compute_initial(self):
        for rec in self:
            if rec.customer_name:
                parts = rec.customer_name.strip().split()
                rec.customer_initial = ''.join([p[0].upper() for p in parts[:2]])
            else:
                rec.customer_initial = '--'

    @api.model_create_multi
    def create(self, vals_list):
        rooms = super().create(vals_list)
        # Skip auto-ticket creation if called from HelpdeskTicketExt.create()
        if getattr(_creating_ticket, 'active', False):
            return rooms
        for room in rooms:
            if not room.ticket_ids:
                # Auto-create a linked helpdesk.ticket (with anti-recursion guard)
                from .helpdesk_ticket import _creating_room
                _creating_room.active = True
                try:
                    ticket_vals = {
                        'name': room.name or 'Ticket',
                        'channel_id': room.id,
                        'priority': '0',
                    }
                    if room.customer_name:
                        ticket_vals['partner_name'] = room.customer_name
                    default_team = self.env['helpdesk.team'].sudo().search([], limit=1)
                    if default_team:
                        ticket_vals['team_id'] = default_team.id
                    self.env['helpdesk.ticket'].sudo().create(ticket_vals)
                finally:
                    _creating_room.active = False
        return rooms

    def write(self, vals):
        res = super().write(vals)
        # Cascade: closing room → close linked ticket
        if 'state' in vals and vals['state'] in ('done', 'archived'):
            if not self.env.context.get('skip_ticket_cascade'):
                for room in self:
                    ticket = room.ticket_ids[:1]
                    if ticket and ticket.stage_id and not ticket.stage_id.fold:
                        fold_stage = self.env['helpdesk.stage'].sudo().search(
                            [('fold', '=', True)], order='sequence asc', limit=1,
                        )
                        if fold_stage:
                            ticket.sudo().with_context(skip_room_cascade=True).write({
                                'stage_id': fold_stage.id,
                            })
        return res

    def unlink(self):
        """Detach ticket references before deleting rooms."""
        for room in self:
            for ticket in room.ticket_ids:
                ticket.sudo().write({'channel_id': False})
        return super().unlink()

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
            # Display name = ticket name (chat bubble title), fallback to room name
            'name': ticket.name if ticket else self.name,
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
            # Ticket info (helpdesk.ticket)
            'ticket_id': ticket.id if ticket else None,
            'ticket_subject': ticket.name if ticket else '',
            'ticket_priority': ticket.priority if ticket else '',
            'ticket_state': ticket.stage_id.name if ticket and ticket.stage_id else '',
        }
