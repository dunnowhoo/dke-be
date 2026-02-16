# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResPartner(models.Model):
    """Extend res.partner with DKE CRM fields."""
    _inherit = 'res.partner'

    # CRM-specific fields
    is_dke_customer = fields.Boolean(string='DKE Customer', default=False)
    marketplace_customer_id = fields.Char(string='Marketplace Customer ID')

    # Purchase behavior
    total_spent = fields.Float(string='Total Spent')
    last_order_date = fields.Date(string='Last Order Date')
    order_count = fields.Integer(string='Order Count')

    # Chat
    chat_room_ids = fields.One2many('dke.chat.room', 'customer_id', string='Chat Rooms')

    # Sentiment
    mood_score = fields.Float(string='Mood Score')
    last_sentiment = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], string='Last Sentiment')
