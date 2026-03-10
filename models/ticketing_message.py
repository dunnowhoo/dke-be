# -*- coding: utf-8 -*-

from odoo import models, fields, api


class TicketingMessage(models.Model):
    """Individual Ticketing Message within a room.

    EPIC01 - PBI-3, PBI-4
    Supports text, image, video, file/document messages with ACID-safe storage.
    """
    _name = 'dke.ticketing.message'
    _description = 'Ticketing Message'
    _order = 'created_at asc'

    room_id = fields.Many2one(
        'dke.ticketing.room', string='Ticketing Room',
        required=True, ondelete='cascade',
        index=True,
    )
    session_id = fields.Many2one(
        'dke.ticketing.session', string='Ticketing Session',
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
        ('video', 'Video'),
        ('document', 'Document'),
        ('file', 'File'),
    ], string='Message Type', default='text')

    # Attachment — stored as ir.attachment for ACID-safe binary storage
    attachment_url = fields.Char(string='Attachment URL')
    attachment_id = fields.Many2one(
        'ir.attachment', string='Attachment',
        ondelete='set null',
        help='Reference to Odoo ir.attachment for ACID-safe binary storage'
    )
    attachment_name = fields.Char(string='Attachment Filename')
    attachment_size = fields.Integer(string='Attachment Size (bytes)')
    attachment_mimetype = fields.Char(string='Attachment MIME Type')

    # Status
    is_read = fields.Boolean(string='Read', default=False, index=True)
    is_automated = fields.Boolean(string='Automated Message', default=False)
    send_status = fields.Selection([
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('pending', 'Pending'),
    ], string='Send Status', default='sent')

    # Timestamps
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now, index=True)

    def to_dict(self):
        """Serialize message to dictionary for API response."""
        self.ensure_one()
        att_url = self.attachment_url or ''
        if not att_url and self.attachment_id:
            att_url = '/web/content/%d?download=true' % self.attachment_id.id

        return {
            'id': self.id,
            'room_id': self.room_id.id,
            'session_id': self.session_id.id if self.session_id else None,
            'sender_type': self.sender_type,
            'sender_id': self.sender_id.id if self.sender_id else None,
            'agent_name': self.agent_name or '',
            'content_text': self.content_text or '',
            'message_type': self.message_type,
            'attachment_url': att_url,
            'attachment_name': self.attachment_name or '',
            'attachment_size': self.attachment_size or 0,
            'attachment_mimetype': self.attachment_mimetype or '',
            'is_read': self.is_read,
            'send_status': self.send_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
