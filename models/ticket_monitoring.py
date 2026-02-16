# -*- coding: utf-8 -*-

from odoo import models, fields, api


class TicketMonitoring(models.Model):
    """Ticket SLA monitoring for Expert Staff performance.

    EPIC08 - PBI-24, PBI-25
    """
    _name = 'dke.ticket.monitoring'
    _description = 'Ticket Monitoring'
    _order = 'record_date desc'

    ticket_id = fields.Many2one('dke.support.ticket', string='Support Ticket', required=True)
    expert_user_id = fields.Many2one('res.users', string='Expert Staff')

    # Response Metrics
    response_time_seconds = fields.Integer(string='Response Time (sec)')
    resolution_time_seconds = fields.Integer(string='Resolution Time (sec)')
    record_date = fields.Date(string='Record Date')

    # SLA
    sla_met = fields.Boolean(string='SLA Met', default=True)

    # Warning
    is_warned = fields.Boolean(string='Warning Sent', default=False)
    warned_at = fields.Datetime(string='Warning Sent At')
    warned_by_id = fields.Many2one('res.users', string='Warned By')
