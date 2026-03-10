# -*- coding: utf-8 -*-

from odoo import models, fields, api


class SupportTicket(models.Model):
    """Support ticket for escalating customer issues.

    Created by Customer Care, resolved by Expert Staff.

    EPIC06 - PBI-17, PBI-18, PBI-19, PBI-20
    """
    _name = 'dke.support.ticket'
    _description = 'Support Ticket'
    _order = 'create_date desc'

    name = fields.Char(string='Ticket Reference', required=True, copy=False)
    subject = fields.Char(string='Subject')
    description = fields.Text(string='Description')

    # Relations
    room_id = fields.Many2one('dke.ticketing.room', string='Source Chat Room', ondelete='set null')
    customer_id = fields.Many2one('res.partner', string='Customer')

    # Assigned Staff
    created_by_id = fields.Many2one('res.users', string='Created By (Customer Care)')
    assigned_expert_id = fields.Many2one('res.users', string='Assigned Expert Staff')

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

    category = fields.Selection([
        ('face_wash', 'Face Wash'),
        ('serum', 'Serum'),
        ('lotion', 'Lotion'),
        ('toner', 'Toner'),
    ], string='Category')

    # SLA
    sla_deadline = fields.Datetime(string='SLA Deadline')
    is_overdue = fields.Boolean(string='Is Overdue', default=False)

    # Timestamps
    first_response_at = fields.Datetime(string='First Response Time')
    resolved_at = fields.Datetime(string='Resolved Time')

    # Internal communication
    ticket_message_ids = fields.One2many(
        'dke.support.ticket.message', 'ticket_id', string='Ticket Messages'
    )


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
