# -*- coding: utf-8 -*-

from odoo import models, fields


class DkeWaTemplate(models.Model):
    """Custom WhatsApp template managed entirely within Propenheimer.

    Submits templates to Meta Graph API using the existing whatsapp.account
    credentials.  Does not depend on Odoo's native whatsapp.template model.

    EPIC03 - PBI-8
    """
    _name = 'dke.wa.template'
    _description = 'DKE WhatsApp Template'
    _order = 'create_date desc'

    name = fields.Char(string='Template Name', required=True)

    # The slug sent to Meta (lowercase, alphanumeric, underscores only)
    meta_template_name = fields.Char(
        string='Meta Template Name',
        required=True,
        help='Lowercase alphanumeric + underscore name registered with Meta.',
    )

    # Meta's internal ID returned after submission
    meta_template_id = fields.Char(string='Meta Template ID', readonly=True)

    body = fields.Text(
        string='Message Body',
        required=True,
        help='Plain text body of the template.  Variables use {{1}}, {{2}}, etc.',
    )

    lang_code = fields.Char(
        string='Language Code',
        default='id',
        required=True,
        help='BCP-47 language code, e.g. "id" (Indonesian) or "en_US".',
    )

    category = fields.Selection([
        ('MARKETING', 'Marketing'),
        ('UTILITY', 'Utility'),
    ], string='Category', default='MARKETING', required=True)

    status = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted to Meta'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', required=True)

    rejection_reason = fields.Char(string='Rejection Reason', readonly=True)
