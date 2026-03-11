# -*- coding: utf-8 -*-

from odoo import models, fields, api


class SaleTransaction(models.Model):
    """Marketplace sale transaction synced from external platforms.

    EPIC04 - PBI-9, PBI-10, PBI-11, PBI-12
    """
    _name = 'dke.sale.transaction'
    _description = 'Marketplace Sale Transaction'
    _order = 'order_date desc'

    name = fields.Char(string='Order Reference', required=True)
    external_order_id = fields.Char(string='External Order ID', index=True)

    # Customer
    customer_id = fields.Many2one('res.partner', string='Customer')
    customer_name = fields.Char(string='Customer Name')
    customer_phone = fields.Char(string='Customer Phone')
    shipping_address = fields.Text(string='Shipping Address')

    # Source
    marketplace = fields.Selection([
        ('shopee', 'Shopee'),
        ('tiktok', 'TikTok Shop'),
        ('tokopedia', 'Tokopedia'),
    ], string='Marketplace')
    integration_id = fields.Many2one('dke.marketplace.integration', string='Integration')

    # Dates
    order_date = fields.Datetime(string='Order Date')
    delivery_date = fields.Datetime(string='Delivery Date')

    # Financial
    amount_gross = fields.Float(string='Gross Amount')
    marketplace_fee = fields.Float(string='Marketplace Fee')
    shipping_subsidy = fields.Float(string='Shipping Subsidy')
    amount_net = fields.Float(string='Net Amount')

    # Status
    state = fields.Selection([
        ('draft', 'Draft / Unpaid'),
        ('processing', 'Processing'),
        ('delivery', 'Delivery Order'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft')

    # Order lines
    line_ids = fields.One2many('dke.sale.transaction.line', 'transaction_id', string='Order Lines')

    # Link to Odoo sale order
    sale_order_id = fields.Many2one('sale.order', string='Odoo Sale Order')


class SaleTransactionLine(models.Model):
    """Order line item for marketplace transactions."""
    _name = 'dke.sale.transaction.line'
    _description = 'Transaction Line Item'

    transaction_id = fields.Many2one(
        'dke.sale.transaction', string='Transaction', required=True, ondelete='cascade'
    )
    product_id = fields.Many2one('product.product', string='Product')
    product_name = fields.Char(string='Product Name')
    quantity = fields.Integer(string='Quantity', default=1)
    unit_price = fields.Float(string='Unit Price')
    subtotal = fields.Float(string='Subtotal')
