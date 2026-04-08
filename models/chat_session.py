# -*- coding: utf-8 -*-

from odoo import models, fields


class ChatSession(models.Model):
    """Lifecycle session within a single chat room.

    One contact should map to one chat room, while each close action
    ends the current session and starts a new one.
    """

    _name = 'dke.chat.session'
    _description = 'Chat Session'
    _order = 'started_at desc'

    session_code = fields.Char(
        string='Session Code',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('dke.chat.session') or 'CHAT/NEW',
    )
    room_id = fields.Many2one(
        'dke.chat.room',
        string='Chat Room',
        required=True,
        ondelete='cascade',
    )

    cs_user_id = fields.Many2one(
        'res.users',
        string='Customer Care',
        help='Customer care assigned to this session',
    )

    state = fields.Selection([
        ('active', 'Active'),
        ('closed', 'Closed'),
    ], string='Status', default='active')

    started_at = fields.Datetime(
        string='Started At',
        default=fields.Datetime.now,
    )
    ended_at = fields.Datetime(string='Ended At')

    def action_close(self):
        """Close session and set ended timestamp."""
        for rec in self:
            rec.write({
                'state': 'closed',
                'ended_at': fields.Datetime.now(),
            })
