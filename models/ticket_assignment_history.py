# -*- coding: utf-8 -*-

from odoo import models, fields


class TicketAssignmentHistory(models.Model):
    """Track assignment changes for helpdesk tickets."""
    _name = 'dke.ticket.assignment.history'
    _description = 'Ticket Assignment History'
    _order = 'assigned_at desc'

    ticket_id = fields.Many2one(
        'helpdesk.ticket', string='Ticket',
        required=True, ondelete='cascade',
    )
    assigned_from_id = fields.Many2one(
        'res.users', string='Assigned From',
        ondelete='set null',
    )
    assigned_to_id = fields.Many2one(
        'res.users', string='Assigned To',
        required=True, ondelete='restrict',
    )
    assigned_by_id = fields.Many2one(
        'res.users', string='Assigned By',
        ondelete='set null',
    )
    reason = fields.Text(string='Reason')
    assigned_at = fields.Datetime(
        string='Assigned At', default=fields.Datetime.now,
    )
