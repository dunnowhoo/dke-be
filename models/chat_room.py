# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ChatRoom(models.Model):
    """Chat room / conversation thread.

    Represents a conversation between Customer Care and a customer,
    sourced from marketplace (Shopee) or WhatsApp.

    Inherits mail.thread for Odoo Discuss integration — every chat
    message is also posted to the chatter so CS can read/reply from
    the Discuss inbox.

    EPIC01 - PBI-1, PBI-2, PBI-6
    """
    _name = 'dke.chat.room'
    _inherit = ['mail.thread']
    _description = 'Chat Room'
    _order = 'last_message_time desc'

    name = fields.Char(string='Room Name', required=True)
    customer_name = fields.Char(string='Customer Name')
    customer_id = fields.Many2one('res.partner', string='Customer')
    assigned_care_id = fields.Many2one('res.users', string='Assigned Customer Care')

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

    # Discuss / WhatsApp integration
    discuss_channel_id = fields.Many2one(
        'discuss.channel',
        string='Discuss Channel',
        help='Link to native Odoo WhatsApp discuss.channel for read/reply integration.',
    )

    # Relations
    message_ids = fields.One2many('dke.chat.message', 'room_id', string='Messages')
    ticket_ids = fields.One2many('dke.support.ticket', 'room_id', string='Support Tickets')
    scheduled_message_ids = fields.One2many(
        'dke.scheduled.message', 'room_id', string='Scheduled Messages'
    )
