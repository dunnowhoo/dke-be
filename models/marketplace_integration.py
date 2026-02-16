# -*- coding: utf-8 -*-

from odoo import models, fields, api


class MarketplaceIntegration(models.Model):
    """Marketplace API connection configuration.

    Manages OAuth tokens and sync state for external marketplaces
    (Shopee, TikTok Shop, Tokopedia).

    EPIC01 - PBI-1
    """
    _name = 'dke.marketplace.integration'
    _description = 'Marketplace Integration'

    name = fields.Char(string='Integration Name', required=True)
    marketplace = fields.Selection([
        ('shopee', 'Shopee'),
        ('tiktok', 'TikTok Shop'),
        ('tokopedia', 'Tokopedia'),
    ], string='Marketplace', required=True)

    # OAuth / API Credentials
    api_key = fields.Char(string='API Key')
    api_secret = fields.Char(string='API Secret')
    access_token = fields.Char(string='Access Token')
    refresh_token = fields.Char(string='Refresh Token')
    token_expiry = fields.Datetime(string='Token Expiry')

    # Connection Status
    state = fields.Selection([
        ('disconnected', 'Disconnected'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ], string='Status', default='disconnected')

    # Sync Config
    last_sync_time = fields.Datetime(string='Last Sync Time')
    sync_interval_minutes = fields.Integer(string='Sync Interval (min)', default=5)
    shop_id = fields.Char(string='Shop ID')

    active = fields.Boolean(string='Active', default=True)
