# -*- coding: utf-8 -*-

import requests
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class WhatsAppConfig(models.Model):
    """WhatsApp Business Integration Configuration.

    Stores API credentials and connection status for WhatsApp Business API.
    Only ONE active config should exist at a time (singleton pattern).

    EPIC02 - PBI-7
    """
    _name = 'dke.whatsapp.config'
    _description = 'WhatsApp Integration Configuration'

    name = fields.Char(
        string='Name',
        default='WhatsApp Business Integration',
        required=True,
    )

    # Credentials
    api_token = fields.Char(string='API Token')
    phone_number_id = fields.Char(string='Phone Number ID')
    webhook_url = fields.Char(string='Webhook URL')
    webhook_verify_token = fields.Char(string='Webhook Verify Token')
    phone_number = fields.Char(string='Connected Phone Number', readonly=True)

    # Connection State
    state = fields.Selection([
        ('disconnected', 'Disconnected'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ], string='Status', default='disconnected', readonly=True)

    last_sync_time = fields.Datetime(string='Last Sync Time', readonly=True)
    active = fields.Boolean(default=True)

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    @api.model
    def get_active_config(self):
        """Return the single active WhatsApp config record (or empty)."""
        return self.search([('active', '=', True)], limit=1)

    def validate_token(self):
        """Validate API token by calling Meta Graph API.

        Returns:
            tuple(bool, str): (success, human-readable message)
        """
        self.ensure_one()

        if not self.api_token:
            return False, 'API Token wajib diisi.'
        if not self.phone_number_id:
            return False, 'Phone Number ID wajib diisi.'

        try:
            url = f'https://graph.facebook.com/v18.0/{self.phone_number_id}'
            headers = {'Authorization': f'Bearer {self.api_token}'}
            resp = requests.get(url, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                self.write({
                    'state': 'connected',
                    'phone_number': data.get('display_phone_number', self.phone_number_id),
                })
                return True, 'Koneksi berhasil.'

            elif resp.status_code == 401:
                self.write({'state': 'error'})
                return False, 'Token tidak valid atau sudah expired (401 Unauthorized).'

            else:
                self.write({'state': 'error'})
                return False, f'Gagal koneksi: HTTP {resp.status_code}.'

        except requests.exceptions.Timeout:
            self.write({'state': 'error'})
            return False, 'Timeout saat menghubungi Meta API.'

        except Exception as e:
            self.write({'state': 'error'})
            _logger.error("WhatsApp token validation error: %s", str(e))
            return False, f'Error tidak terduga: {str(e)}'

    def disconnect(self):
        """Reset config to disconnected state."""
        self.ensure_one()
        self.write({
            'state': 'disconnected',
            'api_token': False,
            'phone_number': False,
        })
