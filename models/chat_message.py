# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ChatMessage(models.Model):
    """Individual chat message within a room.

    EPIC01 - PBI-3, PBI-4
    """
    _name = 'dke.chat.message'
    _description = 'Chat Message'
    _order = 'created_at asc'

    room_id = fields.Many2one(
        'dke.chat.room', string='Chat Room',
        required=True, ondelete='cascade'
    )
    session_id = fields.Many2one(
        'dke.chat.session', string='Chat Session',
        ondelete='set null',
        help='Session this message belongs to'
    )
    external_message_id = fields.Char(string='External Message ID', index=True)

    # Sender
    sender_type = fields.Selection([
        ('customer', 'Customer'),
        ('cs', 'Customer Service'),
        ('ai', 'AI Agent'),
        ('system', 'System Auto-Message'),
    ], string='Sender Type', required=True)
    sender_id = fields.Many2one('res.users', string='Sender (Staff)')
    agent_name = fields.Char(
        string='Agent Name',
        help='Display name of the CS or AI agent who sent the message'
    )

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

    def to_dict(self):
        """Serialize message to dictionary for API response."""
        self.ensure_one()
        return {
            'id': self.id,
            'room_id': self.room_id.id,
            'session_id': self.session_id.id if self.session_id else None,
            'sender_type': self.sender_type,
            'sender_id': self.sender_id.id if self.sender_id else None,
            'agent_name': self.agent_name or '',
            'content_text': self.content_text or '',
            'message_type': self.message_type,
            'attachment_url': self.attachment_url or '',
            'is_read': self.is_read,
            'send_status': self.send_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
