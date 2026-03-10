# -*- coding: utf-8 -*-

import re
import math
import logging

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

VALID_CATEGORIES = ('produk', 'pengiriman', 'pembayaran', 'akun', 'promo', 'teknis', 'lainnya')
VALID_STATUSES = ('draft', 'published')


# ------------------------------------------------------------------ #
#  Helpers                                                              #
# ------------------------------------------------------------------ #

def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def _require_author_or_admin(article):
    """Check if current user is the article author or admin."""
    user = request.env.user
    is_admin = user._is_admin() or user.has_group('base.group_system')
    if not is_admin and article.author_id.id != user.id:
        return _error(403, 'Hanya author atau admin yang dapat mengubah artikel ini.')
    return None


def _strip_html(html_str):
    """Remove HTML tags, return plain text."""
    return re.sub(r'<[^>]+>', '', html_str or '').strip()


def _serialize_article(article, excerpt_length=0):
    """Convert dke.faq.article record to API dict."""
    data = {
        'article_id': article.id,
        'title': article.title or '',
        'category': article.category or 'lainnya',
        'tags': [t.strip() for t in (article.tags or '').split(',') if t.strip()],
        'status': article.status,
        'author_id': article.author_id.id,
        'author_name': article.author_id.name or '',
        'created_at': article.create_date.isoformat() if article.create_date else None,
        'updated_at': article.write_date.isoformat() if article.write_date else None,
        'updated_by': article.updated_by.name if article.updated_by else None,
    }
    if excerpt_length:
        data['excerpt'] = article._get_plain_text(max_length=excerpt_length)
    else:
        data['content'] = article.content or ''
    return data


class FaqController(http.Controller):
    """REST API endpoints for Knowledge Base FAQ.

    EPIC04 - PBI-13: Create article
    EPIC04 - PBI-14: Search & use in chat
    EPIC04 - PBI-15: Update article
    EPIC04 - PBI-16: Delete article (soft)
    """

    # ================================================================== #
    #  POST /api/faq/articles/create — Create (PBI-13)                     #
    # ================================================================== #
    @http.route(
        '/api/faq/articles/create',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_article(self, title='', content='', category='lainnya', tags=None, status='draft', **kwargs):
        """Create a new FAQ article.

        JSON-RPC params:
            { "title": "...", "content": "<p>...</p>", "category": "...", "tags": ["a","b"], "status": "draft"|"published" }

        Response:
            { "status": "success", "data": { article_id, title, status, created_at } }
        """
        try:
            user = request.env.user

            # Permission: sales_manager or expert_staff
            allowed = user._is_admin() or user.dke_role in ('sales_manager', 'expert_staff')
            if not allowed:
                return _error(403, 'Hanya Sales Manager atau Expert Staff yang dapat membuat artikel.')

            title = (title or '').strip()
            content = (content or '').strip()

            if not title:
                return _error(400, 'Judul artikel wajib diisi.')
            if not content:
                return _error(400, 'Konten artikel wajib diisi.')

            plain = _strip_html(content)
            if len(plain) < 50:
                return _error(400, 'Konten artikel harus minimal 50 karakter.')

            if category and category not in VALID_CATEGORIES:
                return _error(400, f'Kategori tidak valid. Pilihan: {", ".join(VALID_CATEGORIES)}')

            if status not in VALID_STATUSES:
                return _error(400, 'Status harus draft atau published.')

            tags_str = ','.join([t.strip() for t in (tags or []) if t.strip()]) if isinstance(tags, list) else (tags or '')

            article = request.env['dke.faq.article'].sudo().create({
                'title': title,
                'content': content,
                'category': category or 'lainnya',
                'tags': tags_str,
                'author_id': user.id,
                'status': status,
                'updated_by': user.id,
            })

            return {
                'status': 'success',
                'data': {
                    'article_id': article.id,
                    'title': article.title,
                    'status': article.status,
                    'created_at': article.create_date.isoformat(),
                },
            }

        except ValidationError as ve:
            return _error(400, str(ve))
        except Exception as exc:
            _logger.exception('FAQ create error')
            return _error(500, str(exc))

    # ================================================================== #
    #  POST /api/faq/articles/list — List & Search (PBI-14)               #
    # ================================================================== #
    @http.route(
        '/api/faq/articles/list',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def list_articles(self, search='', category='', status='', page=1, limit=20, **kwargs):
        """List/search FAQ articles.

        JSON-RPC params (all optional):
            { "search": "keyword", "category": "...", "status": "...", "page": 1, "limit": 20 }

        Response:
            { "status": "success", "data": [...], "total": N, "page": N, "limit": N, "total_pages": N }
        """
        try:
            domain = [('status', '!=', 'deleted')]

            # Full-text search on title, content, tags
            if search:
                search = search.strip()
                domain += [
                    '|', '|',
                    ('title', 'ilike', search),
                    ('content', 'ilike', search),
                    ('tags', 'ilike', search),
                ]

            if category and category in VALID_CATEGORIES:
                domain.append(('category', '=', category))

            if status and status in VALID_STATUSES:
                domain.append(('status', '=', status))

            page = max(1, int(page or 1))
            limit = min(100, max(1, int(limit or 20)))
            offset = (page - 1) * limit

            Article = request.env['dke.faq.article'].sudo()
            total = Article.search_count(domain)
            articles = Article.search(domain, limit=limit, offset=offset, order='write_date desc')

            return {
                'status': 'success',
                'data': [_serialize_article(a, excerpt_length=200) for a in articles],
                'total': total,
                'page': page,
                'limit': limit,
                'total_pages': math.ceil(total / limit) if total else 0,
            }

        except Exception as exc:
            _logger.exception('FAQ list error')
            return _error(500, str(exc))

    # ================================================================== #
    #  POST /api/faq/articles/detail/<id> — Detail (PBI-14)               #
    # ================================================================== #
    @http.route(
        '/api/faq/articles/detail/<int:article_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def get_article(self, article_id, **kwargs):
        """Get full article detail.

        Response:
            { "status": "success", "data": { ...full article with content... } }
        """
        try:
            article = request.env['dke.faq.article'].sudo().browse(article_id)
            if not article.exists() or article.status == 'deleted':
                return _error(404, 'Artikel tidak ditemukan.')

            return {
                'status': 'success',
                'data': _serialize_article(article),
            }

        except Exception as exc:
            _logger.exception('FAQ detail error')
            return _error(500, str(exc))

    # ================================================================== #
    #  POST /api/faq/articles/update/<id> — Update (PBI-15)               #
    # ================================================================== #
    @http.route(
        '/api/faq/articles/update/<int:article_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def update_article(self, article_id, title='', content='', category='', tags=None, status='', **kwargs):
        """Update an existing FAQ article. Saves previous version.

        JSON-RPC params:
            { "title": "...", "content": "...", "category": "...", "tags": [...], "status": "..." }
        """
        try:
            article = request.env['dke.faq.article'].sudo().browse(article_id)
            if not article.exists() or article.status == 'deleted':
                return _error(404, 'Artikel tidak ditemukan.')

            # Permission check
            err = _require_author_or_admin(article)
            if err:
                return err

            user = request.env.user

            # Save version before update (PBI-15: versioning)
            request.env['dke.faq.article.version'].sudo().create({
                'article_id': article.id,
                'title': article.title,
                'content': article.content,
                'category': article.category,
                'tags': article.tags,
                'status': article.status,
                'edited_by': user.id,
            })

            # Build update values
            vals = {'updated_by': user.id}

            if title and title.strip():
                vals['title'] = title.strip()
            if content and content.strip():
                plain = _strip_html(content)
                if len(plain) < 50:
                    return _error(400, 'Konten artikel harus minimal 50 karakter.')
                vals['content'] = content.strip()
            if category:
                if category not in VALID_CATEGORIES:
                    return _error(400, f'Kategori tidak valid.')
                vals['category'] = category
            if tags is not None:
                if isinstance(tags, list):
                    vals['tags'] = ','.join([t.strip() for t in tags if t.strip()])
                else:
                    vals['tags'] = tags
            if status:
                if status not in VALID_STATUSES:
                    return _error(400, 'Status harus draft atau published.')
                vals['status'] = status

            article.write(vals)

            return {
                'status': 'success',
                'data': _serialize_article(article),
            }

        except ValidationError as ve:
            return _error(400, str(ve))
        except Exception as exc:
            _logger.exception('FAQ update error')
            return _error(500, str(exc))

    # ================================================================== #
    #  POST /api/faq/articles/delete/<id> — Soft Delete (PBI-16)          #
    # ================================================================== #
    @http.route(
        '/api/faq/articles/delete/<int:article_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def delete_article(self, article_id, **kwargs):
        """Soft-delete an FAQ article.

        Response:
            { "status": "success", "message": "Artikel berhasil dihapus" }
        """
        try:
            article = request.env['dke.faq.article'].sudo().browse(article_id)
            if not article.exists() or article.status == 'deleted':
                return _error(404, 'Artikel tidak ditemukan.')

            # Permission check
            err = _require_author_or_admin(article)
            if err:
                return err

            article.write({
                'status': 'deleted',
                'deleted_at': fields.Datetime.now(),
            })

            return {
                'status': 'success',
                'message': 'Artikel berhasil dihapus',
            }

        except Exception as exc:
            _logger.exception('FAQ delete error')
            return _error(500, str(exc))
