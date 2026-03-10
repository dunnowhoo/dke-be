# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ChatMessage(models.Model):
    """Individual chat message within a room.

    EPIC01 - PBI-3, PBI-4
    """
    _name = 'dke.chat.message'
    _description = 'Chat Message'
    _order = 'created_at asc'

    room_id = fields.Many2one('dke.chat.room', string='Chat Room', required=True, ondelete='cascade')
    external_message_id = fields.Char(string='External Message ID', index=True)

    # Sender
    sender_type = fields.Selection([
        ('customer', 'Customer'),
        ('admin', 'Admin/Staff'),
        ('system', 'System Auto-Message'),
    ], string='Sender Type', required=True)
    sender_id = fields.Many2one('res.users', string='Sender (Staff)')

    # Content
    content_text = fields.Text(string='Message Content')
    message_type = fields.Selection([
        ('text', 'Text'),
        ('image', 'Image'),
        ('file', 'File'),
    ], string='Message Type', default='text')
    attachment_url = fields.Char(string='Attachment URL')

    # Status
    is_read = fields.Boolean(string='Read', default=False)
    is_automated = fields.Boolean(string='Automated Message', default=False)
    send_status = fields.Selection([
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('pending', 'Pending'),
    ], string='Send Status', default='sent')

    # Timestamps
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now)
