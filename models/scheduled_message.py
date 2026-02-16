# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ScheduledMessage(models.Model):
    """Scheduled follow-up messages.

    Supports both automated follow-ups (Cron-based, EPIC02 PBI-7)
    and manually scheduled messages (EPIC02 PBI-8).

    EPIC02 - PBI-7, PBI-8
    """
    _name = 'dke.scheduled.message'
    _description = 'Scheduled Message'
    _order = 'send_at asc'

    room_id = fields.Many2one('dke.chat.room', string='Chat Room', required=True, ondelete='cascade')
    customer_id = fields.Many2one('res.partner', string='Customer')
    created_by_id = fields.Many2one('res.users', string='Created By')

    # Content
    message = fields.Text(string='Message Content', required=True)

    # Schedule
    send_at = fields.Datetime(string='Scheduled Send Time', required=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ], string='Status', default='pending')

    # Type
    schedule_type = fields.Selection([
        ('auto_followup', 'Auto Follow-Up (System)'),
        ('manual', 'Manual Schedule'),
    ], string='Schedule Type', default='manual')

    # Reference
    sale_order_id = fields.Many2one('sale.order', string='Related Sale Order')
    sent_at = fields.Datetime(string='Actually Sent At')
    error_message = fields.Text(string='Error Message')
