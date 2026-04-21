# -*- coding: utf-8 -*-

from odoo import models, fields


class Announcement(models.Model):
    """Internal announcements visible by role."""

    _name = 'dke.announcement'
    _description = 'DKE Announcement'
    _order = 'created_at desc'

    title = fields.Char(string='Title', required=True, index=True)
    content = fields.Html(string='Content', sanitize=True, required=True)
    target_role = fields.Selection([
        ('all', 'All Roles'),
        ('customer_care', 'Customer Care'),
        ('expert_staff', 'Expert Staff'),
    ], string='Target Role', default='all', required=True, index=True)
    priority = fields.Selection([
        ('normal', 'Normal'),
        ('urgent', 'Urgent'),
    ], string='Priority', default='normal', required=True, index=True)
    expiry_date = fields.Date(string='Expiry Date', index=True)

    created_by = fields.Many2one(
        'res.users', string='Created By', required=True, ondelete='restrict', index=True
    )
    created_at = fields.Datetime(string='Created At', required=True, default=fields.Datetime.now, index=True)

    read_ids = fields.One2many('dke.announcement.read', 'announcement_id', string='Read States')


class AnnouncementRead(models.Model):
    """Tracks whether an announcement has been read by a user."""

    _name = 'dke.announcement.read'
    _description = 'DKE Announcement Read State'
    _order = 'read_at desc'

    announcement_id = fields.Many2one(
        'dke.announcement', string='Announcement', required=True, ondelete='cascade', index=True
    )
    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='cascade', index=True
    )
    read_at = fields.Datetime(string='Read At', required=True, default=fields.Datetime.now)

    _sql_constraints = [
        (
            'uniq_announcement_user_read',
            'unique(announcement_id, user_id)',
            'Read state untuk user ini sudah ada.',
        )
    ]
