# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResUsers(models.Model):
    """Extend res.users with DKE CRM role fields."""
    _inherit = 'res.users'

    dke_role = fields.Selection([
        ('customer_care', 'Customer Care'),
        ('sales_staff', 'Sales Staff'),
        ('sales_manager', 'Sales Manager'),
        ('expert_staff', 'Expert Staff'),
    ], string='DKE Role')

    dke_status = fields.Selection([
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ], string='DKE Status', default='active')

    dke_specialization = fields.Selection([
        ('face_wash', 'Face Wash'),
        ('serum', 'Serum'),
        ('lotion', 'Lotion'),
        ('toner', 'Toner'),
    ], string='Specialization')

    dke_phone = fields.Char(string='DKE Phone')

    # Performance stats (computed — to be implemented)
    avg_response_time = fields.Float(string='Avg Response Time (min)')
    avg_rating = fields.Float(string='Avg Customer Rating')
    total_chats_handled = fields.Integer(string='Total Chats Handled')
    total_tickets_resolved = fields.Integer(string='Total Tickets Resolved')
