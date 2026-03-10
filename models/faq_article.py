# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class FaqArticle(models.Model):
    """Knowledge Base FAQ article for internal reference.

    EPIC04 - PBI-13, PBI-14, PBI-15, PBI-16
    """
    _name = 'dke.faq.article'
    _description = 'FAQ Article'
    _order = 'write_date desc'

    title = fields.Char(string='Title', required=True, index=True)
    content = fields.Html(string='Content', sanitize=True)
    category = fields.Selection([
        ('produk', 'Produk'),
        ('pengiriman', 'Pengiriman'),
        ('pembayaran', 'Pembayaran'),
        ('akun', 'Akun'),
        ('promo', 'Promo'),
        ('teknis', 'Teknis'),
        ('lainnya', 'Lainnya'),
    ], string='Category', default='lainnya', index=True)
    tags = fields.Char(string='Tags', help='Comma-separated tags for search')

    # Author & permissions
    author_id = fields.Many2one('res.users', string='Author', required=True, ondelete='restrict')

    # Status
    status = fields.Selection([
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('deleted', 'Deleted'),
    ], string='Status', default='draft', index=True)

    # Soft delete
    deleted_at = fields.Datetime(string='Deleted At')

    # Tracking
    updated_by = fields.Many2one('res.users', string='Last Updated By')

    # Versioning
    version_ids = fields.One2many('dke.faq.article.version', 'article_id', string='Versions')

    @api.constrains('content')
    def _check_content_length(self):
        """Content must be at least 50 characters (stripped of HTML tags)."""
        for rec in self:
            if rec.content:
                import re
                text = re.sub(r'<[^>]+>', '', rec.content or '').strip()
                if len(text) < 50:
                    raise ValidationError('Konten artikel harus minimal 50 karakter.')

    def _get_plain_text(self, max_length=0):
        """Strip HTML tags and optionally truncate."""
        import re
        text = re.sub(r'<[^>]+>', '', self.content or '').strip()
        if max_length and len(text) > max_length:
            return text[:max_length].rstrip() + '...'
        return text


class FaqArticleVersion(models.Model):
    """Stores previous versions of FAQ articles for audit trail.

    EPIC04 - PBI-15 requirement: versioning on update.
    """
    _name = 'dke.faq.article.version'
    _description = 'FAQ Article Version'
    _order = 'created_at desc'

    article_id = fields.Many2one(
        'dke.faq.article', string='Article', required=True, ondelete='cascade'
    )
    title = fields.Char(string='Title')
    content = fields.Html(string='Content')
    category = fields.Selection([
        ('produk', 'Produk'),
        ('pengiriman', 'Pengiriman'),
        ('pembayaran', 'Pembayaran'),
        ('akun', 'Akun'),
        ('promo', 'Promo'),
        ('teknis', 'Teknis'),
        ('lainnya', 'Lainnya'),
    ], string='Category')
    tags = fields.Char(string='Tags')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('deleted', 'Deleted'),
    ], string='Status')
    edited_by = fields.Many2one('res.users', string='Edited By', required=True)
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now)
