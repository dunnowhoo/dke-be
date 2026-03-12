# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError


class SupportTicket(models.Model):
    """Support ticket for escalating customer issues.

    Created by Customer Care, resolved by Expert Staff.
    Has a 1-to-1 link with a dke.ticketing.room (the chat bubble).
    The ticket subject is used as the chat room display name.

    EPIC06 - PBI-17, PBI-18, PBI-19, PBI-20
    """
    _name = 'dke.support.ticket'
    _description = 'Support Ticket'
    _order = 'create_date desc'

    name = fields.Char(string='Ticket Reference', required=True, copy=False)
    # subject = title of the chat bubble shown in the UI
    subject = fields.Char(string='Subject / Chat Title', required=True)
    description = fields.Text(string='Description')

    # Relations — 1-to-1 link with chat room
    room_id = fields.Many2one(
        'dke.ticketing.room', string='Chat Room', ondelete='cascade',
        help='1-to-1 linked chat bubble for this ticket.',
    )
    customer_id = fields.Many2one('res.partner', string='Customer')

    # Assigned Staff
    created_by_id = fields.Many2one('res.users', string='Created By (Customer Care)')
    assigned_expert_id = fields.Many2one(
        'res.users', string='Assigned Expert Staff',
        domain=[('dke_role', '=', 'expert_staff')],
    )
    # Denormalized for quick display without join
    assigned_expert_specialization = fields.Char(
        string='Expert Specialization',
        compute='_compute_expert_info', store=True,
    )
    assigned_expert_name = fields.Char(
        string='Expert Name',
        compute='_compute_expert_info', store=True,
    )

    # Priority & Status
    priority = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], string='Priority', default='medium')

    state = fields.Selection([
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ], string='Status', default='open')

    # Topic & Specialization for filtering
    topic = fields.Selection([
        ('product_inquiry', 'Product Inquiry'),
        ('order_complaint', 'Order Complaint'),
        ('return_refund', 'Return / Refund'),
        ('shipment', 'Shipment Issue'),
        ('payment', 'Payment Issue'),
        ('other', 'Other'),
    ], string='Topic', default='other')

    required_specialization = fields.Selection([
        ('face_wash', 'Face Wash'),
        ('serum', 'Serum'),
        ('lotion', 'Lotion'),
        ('toner', 'Toner'),
    ], string='Required Specialization',
        help='Specialization needed to resolve this ticket; used when assigning expert staff.')

    category = fields.Selection([
        ('face_wash', 'Face Wash'),
        ('serum', 'Serum'),
        ('lotion', 'Lotion'),
        ('toner', 'Toner'),
    ], string='Category')

    # SLA
    sla_deadline = fields.Datetime(string='SLA Deadline')
    is_overdue = fields.Boolean(string='Is Overdue', default=False, compute='_compute_is_overdue', store=True)

    # Timestamps
    first_response_at = fields.Datetime(string='First Response Time')
    resolved_at = fields.Datetime(string='Resolved Time')

    # Performance metrics
    resolution_time_hours = fields.Float(
        string='Resolution Time (hours)',
        compute='_compute_resolution_metrics', store=True,
        help='Time from ticket creation to resolution in hours.',
    )
    message_count = fields.Integer(
        string='Messages in Chat',
        compute='_compute_resolution_metrics', store=True,
        help='Total messages in the linked chat room.',
    )

    # Internal communication
    ticket_message_ids = fields.One2many(
        'dke.support.ticket.message', 'ticket_id', string='Ticket Messages'
    )

    # ------------------------------------------------------------------ #
    #  Computed fields                                                     #
    # ------------------------------------------------------------------ #

    @api.depends('assigned_expert_id', 'assigned_expert_id.dke_specialization', 'assigned_expert_id.name')
    def _compute_expert_info(self):
        for rec in self:
            if rec.assigned_expert_id:
                rec.assigned_expert_name = rec.assigned_expert_id.name or ''
                rec.assigned_expert_specialization = rec.assigned_expert_id.dke_specialization or ''
            else:
                rec.assigned_expert_name = ''
                rec.assigned_expert_specialization = ''

    @api.depends('sla_deadline')
    def _compute_is_overdue(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_overdue = bool(
                rec.sla_deadline and rec.state not in ('resolved', 'closed') and rec.sla_deadline < now
            )

    @api.depends('resolved_at', 'create_date', 'room_id', 'room_id.message_ids')
    def _compute_resolution_metrics(self):
        for rec in self:
            # Resolution time
            if rec.resolved_at and rec.create_date:
                delta = rec.resolved_at - rec.create_date
                rec.resolution_time_hours = round(delta.total_seconds() / 3600, 2)
            else:
                rec.resolution_time_hours = 0.0
            # Message count from linked room
            rec.message_count = len(rec.room_id.message_ids) if rec.room_id else 0

    # ------------------------------------------------------------------ #
    #  State transitions                                                   #
    # ------------------------------------------------------------------ #

    def action_mark_in_progress(self):
        for rec in self:
            if not rec.assigned_expert_id:
                raise UserError('Ticket harus di-assign ke Expert Staff terlebih dahulu.')
            rec.write({'state': 'in_progress', 'first_response_at': rec.first_response_at or fields.Datetime.now()})

    def action_resolve(self):
        for rec in self:
            rec.write({'state': 'resolved', 'resolved_at': fields.Datetime.now()})
            # Update expert performance stats
            if rec.assigned_expert_id:
                rec.assigned_expert_id.sudo()._recompute_expert_stats()

    def action_close(self):
        for rec in self:
            if not rec.resolved_at:
                rec.resolved_at = fields.Datetime.now()
            rec.write({'state': 'closed'})
            if rec.assigned_expert_id:
                rec.assigned_expert_id.sudo()._recompute_expert_stats()

    def action_open_chat(self):
        """Navigate from ticket to the linked chat room."""
        self.ensure_one()
        if not self.room_id:
            raise UserError('Tiket ini belum memiliki chat room yang terhubung.')
        return {
            'type': 'ir.actions.act_window',
            'name': self.subject,
            'res_model': 'dke.ticketing.room',
            'res_id': self.room_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  CRUD overrides — keep ticket and chat room in sync                  #
    # ------------------------------------------------------------------ #

    def write(self, vals):
        """When subject changes, propagate the new name to the linked chat room."""
        result = super().write(vals)
        if 'subject' in vals:
            for rec in self:
                if rec.room_id:
                    rec.room_id.sudo().write({'name': vals['subject']})
        return result

    def unlink(self):
        """Delete linked chat room(s) together with the ticket."""
        rooms = self.mapped('room_id').filtered(lambda r: r.id)
        result = super().unlink()
        if rooms:
            rooms.sudo().unlink()
        return result


class SupportTicketMessage(models.Model):
    """Internal messages on a support ticket (CC ↔ Expert Staff)."""
    _name = 'dke.support.ticket.message'
    _description = 'Ticket Message'
    _order = 'created_at asc'

    ticket_id = fields.Many2one(
        'dke.support.ticket', string='Ticket', required=True, ondelete='cascade'
    )
    sender_id = fields.Many2one('res.users', string='Sender', required=True)
    content = fields.Text(string='Message Content', required=True)
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now)
