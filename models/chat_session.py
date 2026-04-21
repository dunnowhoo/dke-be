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

    # Timestamps per ERD
    started_at = fields.Datetime(
        string='Started At',
        default=fields.Datetime.now,
        help='Waktu Sesi Dimulai (Chat Masuk)',
    )
    assigned_at = fields.Datetime(
        string='Assigned At',
        help='Waktu CC Klik Ambil',
    )
    first_response_at = fields.Datetime(
        string='First Response At',
        help='Waktu CC Balas Pertama',
    )
    closed_at = fields.Datetime(
        string='Closed At',
        help='Waktu Sesi Ditutup',
    )

    close_type = fields.Selection([
        ('manual', 'Manual'),
        ('auto_closed', 'Auto Closed'),
    ], string='Close Type', help='manual / auto_closed')

    # Relations
    survey_ids = fields.One2many(
        'dke.customer.survey', 'session_id', string='Surveys',
    )

    def action_close(self, close_type='manual'):
        """Close session and set ended timestamp."""
        for rec in self:
            rec.write({
                'state': 'closed',
                'closed_at': fields.Datetime.now(),
                'close_type': close_type,
            })
