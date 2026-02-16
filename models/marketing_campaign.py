# -*- coding: utf-8 -*-

from odoo import models, fields, api


class MarketingCampaign(models.Model):
    """Marketing campaign for personalized promo delivery.

    EPIC03 - PBI-6, PBI-8
    """
    _name = 'dke.marketing.campaign'
    _description = 'Marketing Campaign'
    _order = 'create_date desc'

    name = fields.Char(string='Campaign Title', required=True)
    description = fields.Text(string='Description')

    # Product & Discount
    product_id = fields.Many2one('product.product', string='Product')
    discount_type = fields.Selection([
        ('fixed', 'Fixed Amount'),
        ('percent', 'Percentage'),
    ], string='Discount Type')
    discount_value = fields.Float(string='Discount Value')

    # Image
    image = fields.Binary(string='Campaign Image')
    image_url = fields.Char(string='Image URL')

    # Status Workflow
    state = fields.Selection([
        ('draft', 'Draft'),
        ('targeted', 'Targeted'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft')

    # Target & Segmentation
    segment_id = fields.Many2one('dke.customer.segment', string='Customer Segment')
    target_audience_ids = fields.Many2many('res.partner', string='Target Audience')
    matched_count = fields.Integer(string='Matched Customers', default=0)

    # Broadcasting Progress
    sent_count = fields.Integer(string='Sent Count', default=0)
    failed_count = fields.Integer(string='Failed Count', default=0)

    # Created By
    created_by_id = fields.Many2one('res.users', string='Created By')
