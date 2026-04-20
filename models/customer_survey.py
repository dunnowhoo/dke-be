# -*- coding: utf-8 -*-

import uuid

from odoo import models, fields


class CustomerSurvey(models.Model):
    """Customer satisfaction survey linked to a closed chat session."""

    _name = 'dke.customer.survey'
    _description = 'Customer Survey'
    _order = 'create_date desc'

    token = fields.Char(
        string='Token',
        required=True,
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: str(uuid.uuid4()),
        help='Unique link URL token for the survey',
    )

    rating = fields.Integer(
        string='Rating',
        help='1-5 Bintang',
    )

    review_text = fields.Text(
        string='Review Text',
        help='Komentar/Ulasan',
    )

    submitted_at = fields.Datetime(
        string='Submitted At',
        help='Waktu Disubmit',
    )

    session_id = fields.Many2one(
        'dke.chat.session',
        string='Chat Session',
        required=True,
        ondelete='cascade',
        help='Referensi Sesi Chat',
    )

    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        help='Pelanggan Penilai',
    )
