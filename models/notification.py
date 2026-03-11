# -*- coding: utf-8 -*-

from odoo import models, fields


class Notification(models.Model):
    """In-app notification for DKE users.

    Used for ticket reassignment alerts, new assignments, etc.
    """
    _name = 'dke.notification'
    _description = 'DKE Notification'
    _order = 'create_date desc'

    user_id = fields.Many2one(
        'res.users', string='Recipient', required=True, ondelete='cascade',
        index=True,
    )
    title = fields.Char(string='Title', required=True)
    message = fields.Text(string='Message')
    notification_type = fields.Selection([
        ('ticket_assigned', 'Ticket Assigned'),
        ('ticket_reassigned', 'Ticket Reassigned'),
        ('ticket_resolved', 'Ticket Resolved'),
        ('chat_assigned', 'Chat Assigned'),
        ('general', 'General'),
    ], string='Type', default='general')

    is_read = fields.Boolean(string='Read', default=False, index=True)

    # Optional reference to a related record
    reference_model = fields.Char(string='Reference Model')
    reference_id = fields.Integer(string='Reference ID')
