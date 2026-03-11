# -*- coding: utf-8 -*-

from odoo import models, fields, api


class TicketingMonitoring(models.Model):
    """Ticketing response monitoring for Customer Care performance.

    EPIC07 - PBI-22, PBI-23
    """
    _name = 'dke.ticketing.monitoring'
    _description = 'Ticketing Monitoring'
    _order = 'record_date desc'

    room_id = fields.Many2one('dke.ticketing.room', string='Ticketing Room', required=True)
    care_user_id = fields.Many2one('res.users', string='Customer Care')
    customer_id = fields.Many2one('res.partner', string='Customer')

    # Response Metrics
    first_response_seconds = fields.Integer(string='First Response Time (sec)')
    avg_response_seconds = fields.Integer(string='Avg Response Time (sec)')
    total_messages = fields.Integer(string='Total Messages')
    record_date = fields.Date(string='Record Date')

    # Warning
    is_warned = fields.Boolean(string='Warning Sent', default=False)
    warned_at = fields.Datetime(string='Warning Sent At')
    warned_by_id = fields.Many2one('res.users', string='Warned By')

    # Status
    state = fields.Selection([
        ('active', 'Active'),
        ('done', 'Done'),
    ], string='Status', default='active')
