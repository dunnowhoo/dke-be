# -*- coding: utf-8 -*-

from odoo import models, fields, api


class CustomerSegment(models.Model):
    """Customer segmentation rules for targeted campaigns.

    EPIC03 - PBI-7
    """
    _name = 'dke.customer.segment'
    _description = 'Customer Segment'

    name = fields.Char(string='Segment Name', required=True)
    description = fields.Text(string='Description')

    # Rules stored as JSON
    rules_json = fields.Text(string='Segmentation Rules (JSON)')

    # Preview
    matched_count = fields.Integer(string='Matched Customers')
    preview_partner_ids = fields.Many2many(
        'res.partner', string='Preview Customers',
        relation='dke_segment_preview_partner_rel',
    )

    # Relation
    campaign_ids = fields.One2many('dke.marketing.campaign', 'segment_id', string='Campaigns')

    active = fields.Boolean(string='Active', default=True)
