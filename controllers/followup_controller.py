# -*- coding: utf-8 -*-

import logging
import math

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def _error(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def _require_sales_access():
    """Return error dict if user is not Sales Staff, Sales Manager, or Admin."""
    user = request.env.user
    if not (
        user.has_group('dke_crm.group_sales_staff')
        or user.has_group('dke_crm.group_sales_manager')
        or user._is_admin()
    ):
        return _error(403, 'Akses ditolak. Hanya Sales Staff atau Manager yang dapat mengakses.')
    return None


def _serialize_rule(rule):
    wa = rule.wa_template_id
    return {
        'rule_id': rule.id,
        'name': rule.name,
        'trigger_event': rule.trigger_event,
        'delay_days': rule.delay_days,
        'is_active': rule.is_active,
        'wa_template_id': wa.id if wa else None,
        'wa_template_name': wa.name if wa else None,
        'wa_template_status': wa.status if wa else None,
        'wa_template_header_type': wa.header_type if wa else None,
        'wa_template_body': wa.body if wa else None,
        'created_by': rule.created_by_id.name if rule.created_by_id else None,
        'created_at': rule.create_date.isoformat() if rule.create_date else None,
        'updated_at': rule.write_date.isoformat() if rule.write_date else None,
    }


class FollowUpController(http.Controller):
    """REST API endpoints for Follow-Up Rules.

    EPIC05 - PBI-32: CRUD follow-up rules
    EPIC05 - PBI-33: Auto follow-up execution (cron, see followup_rule model)
    """

    # ------------------------------------------------------------------ #
    #  POST /api/followup/rules/create  — Create rule (PBI-32)             #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/create',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def create_rule(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        name = (kwargs.get('name') or '').strip()
        trigger_event = kwargs.get('trigger_event', 'last_day_chat')
        delay_days = kwargs.get('delay_days')
        is_active = kwargs.get('is_active', True)
        wa_template_id = kwargs.get('wa_template_id')

        if not name:
            return _error(400, 'Nama rule wajib diisi.')
        if not wa_template_id:
            return _error(400, 'WhatsApp Template wajib dipilih.')
        if delay_days is None:
            return _error(400, 'Delay wajib diisi.')
        if isinstance(delay_days, (int, float)) and delay_days < 0:
            return _error(400, 'Delay tidak boleh negatif.')

        try:
            delay_days = int(delay_days)
        except (ValueError, TypeError):
            return _error(400, 'Delay harus berupa angka.')

        # Validate WA template exists
        wa_tpl = request.env['whatsapp.template'].sudo().browse(int(wa_template_id))
        if not wa_tpl.exists():
            return _error(400, 'WhatsApp Template tidak ditemukan.')

        try:
            vals = {
                'name': name,
                'trigger_event': trigger_event,
                'delay_days': delay_days,
                'is_active': is_active,
                'wa_template_id': int(wa_template_id),
                'created_by_id': request.env.uid,
            }
            rule = request.env['dke.followup.rule'].sudo().create(vals)
            return {
                'status': 'success',
                'data': _serialize_rule(rule),
            }
        except ValidationError as e:
            return _error(400, str(e))
        except Exception as e:
            _logger.exception('Error creating follow-up rule')
            return _error(500, str(e))

    # ------------------------------------------------------------------ #
    #  POST /api/followup/rules/list  — List rules                         #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/list',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def list_rules(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        page = int(kwargs.get('page', 1))
        limit = min(int(kwargs.get('limit', 20)), 100)
        offset = (page - 1) * limit

        Rule = request.env['dke.followup.rule'].sudo()
        total = Rule.search_count([])
        rules = Rule.search([], offset=offset, limit=limit, order='create_date desc')

        return {
            'status': 'success',
            'data': [_serialize_rule(r) for r in rules],
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'total_pages': math.ceil(total / limit) if limit else 1,
            },
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/rules/detail/<id>  — Get single rule             #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/detail/<int:rule_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def get_rule(self, rule_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        rule = request.env['dke.followup.rule'].sudo().browse(rule_id)
        if not rule.exists():
            return _error(404, 'Rule tidak ditemukan.')

        return {
            'status': 'success',
            'data': _serialize_rule(rule),
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/rules/update/<id>  — Update rule                 #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/update/<int:rule_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def update_rule(self, rule_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        rule = request.env['dke.followup.rule'].sudo().browse(rule_id)
        if not rule.exists():
            return _error(404, 'Rule tidak ditemukan.')

        vals = {}
        if 'name' in kwargs:
            name = (kwargs['name'] or '').strip()
            if not name:
                return _error(400, 'Nama rule wajib diisi.')
            vals['name'] = name
        if 'trigger_event' in kwargs:
            vals['trigger_event'] = kwargs['trigger_event']
        if 'delay_days' in kwargs:
            try:
                dd = int(kwargs['delay_days'])
            except (ValueError, TypeError):
                return _error(400, 'Delay harus berupa angka.')
            if dd < 0:
                return _error(400, 'Delay tidak boleh negatif.')
            vals['delay_days'] = dd
        if 'is_active' in kwargs:
            vals['is_active'] = bool(kwargs['is_active'])
        if 'wa_template_id' in kwargs:
            wt = kwargs['wa_template_id']
            if not wt:
                return _error(400, 'WhatsApp Template wajib dipilih.')
            wa_tpl = request.env['whatsapp.template'].sudo().browse(int(wt))
            if not wa_tpl.exists():
                return _error(400, 'WhatsApp Template tidak ditemukan.')
            vals['wa_template_id'] = int(wt)

        try:
            rule.write(vals)
            return {
                'status': 'success',
                'data': _serialize_rule(rule),
            }
        except ValidationError as e:
            return _error(400, str(e))

    # ------------------------------------------------------------------ #
    #  POST /api/followup/rules/delete/<id>  — Delete rule                 #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/delete/<int:rule_id>',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def delete_rule(self, rule_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        rule = request.env['dke.followup.rule'].sudo().browse(rule_id)
        if not rule.exists():
            return _error(404, 'Rule tidak ditemukan.')

        # Cancel all pending scheduled messages linked to this rule
        pending_msgs = request.env['dke.scheduled.message'].sudo().search([
            ('followup_rule_id', '=', rule_id),
            ('state', '=', 'pending'),
        ])
        if pending_msgs:
            pending_msgs.write({'state': 'cancelled'})
            _logger.info('Cancelled %d pending scheduled messages for rule %s', len(pending_msgs), rule.name)

        rule.unlink()
        return {'status': 'success', 'message': 'Rule berhasil dihapus.'}

    # ------------------------------------------------------------------ #
    #  PATCH /api/followup/rules/<id>/toggle  — Toggle active              #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/rules/<int:rule_id>/toggle',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def toggle_rule(self, rule_id, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        rule = request.env['dke.followup.rule'].sudo().browse(rule_id)
        if not rule.exists():
            return _error(404, 'Rule tidak ditemukan.')

        new_active = not rule.is_active
        rule.write({'is_active': new_active})

        # If deactivated, cancel pending scheduled messages from this rule
        if not new_active:
            pending_msgs = request.env['dke.scheduled.message'].sudo().search([
                ('followup_rule_id', '=', rule_id),
                ('state', '=', 'pending'),
            ])
            if pending_msgs:
                pending_msgs.write({'state': 'cancelled'})

        return {
            'status': 'success',
            'data': _serialize_rule(rule),
        }

    # ------------------------------------------------------------------ #
    #  GET /api/followup/logs  — List follow-up execution logs             #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/logs',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def list_logs(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        page = int(kwargs.get('page', 1))
        limit = min(int(kwargs.get('limit', 20)), 100)
        offset = (page - 1) * limit

        # If template_id is provided, query scheduled messages for that template
        if kwargs.get('template_id'):
            Msg = request.env['dke.scheduled.message'].sudo()
            domain = [
                ('wa_template_id', '=', int(kwargs['template_id'])),
                ('state', 'in', ['sent', 'failed']),
            ]
            total = Msg.search_count(domain)
            msgs = Msg.search(domain, offset=offset, limit=limit, order='id desc')

            return {
                'status': 'success',
                'data': [{
                    'id': m.id,
                    'rule_name': m.followup_rule_id.name if m.followup_rule_id else 'Kirim Manual',
                    'room_name': m.chat_room_id.name if m.chat_room_id else (m.room_id.name if m.room_id else ''),
                    'customer_name': m.customer_id.name if m.customer_id else '',
                    'message_sent': (m.message or '')[:200],
                    'sent_at': m.sent_at.isoformat() if m.sent_at else (m.send_at.isoformat() if m.send_at else None),
                    'state': m.state,
                    'error_message': m.error_message or '',
                } for m in msgs],
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total': total,
                    'total_pages': math.ceil(total / limit) if limit else 1,
                },
            }

        # Fallback: query followup logs by rule_id
        Log = request.env['dke.followup.log'].sudo()
        domain = []
        if kwargs.get('rule_id'):
            domain.append(('rule_id', '=', int(kwargs['rule_id'])))

        total = Log.search_count(domain)
        logs = Log.search(domain, offset=offset, limit=limit, order='sent_at desc')

        return {
            'status': 'success',
            'data': [{
                'id': log.id,
                'rule_name': log.rule_id.name,
                'room_name': log.room_id.name,
                'customer_name': log.customer_id.name if log.customer_id else '',
                'message_sent': log.message_sent,
                'sent_at': log.sent_at.isoformat() if log.sent_at else None,
                'state': log.state,
                'error_message': log.error_message,
            } for log in logs],
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'total_pages': math.ceil(total / limit) if limit else 1,
            },
        }

    # ------------------------------------------------------------------ #
    #  GET /api/followup/wa-templates  — List approved WhatsApp templates   #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/wa-templates',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def list_wa_templates(self, **kwargs):
        err = _require_sales_access()
        if err:
            return err

        WaTemplate = request.env['whatsapp.template'].sudo()
        templates = WaTemplate.search([
            ('status', '=', 'approved'),
        ], order='name asc')

        data = []
        for t in templates:
            data.append({
                'id': t.id,
                'name': t.name,
                'template_name': t.template_name,
                'status': t.status,
                'header_type': t.header_type,
                'body': t.body,
                'footer_text': t.footer_text or '',
                'template_type': t.template_type,
                'lang_code': t.lang_code,
                'account_name': t.wa_account_id.name if t.wa_account_id else '',
            })

        return {
            'status': 'success',
            'data': data,
        }

    # ------------------------------------------------------------------ #
    #  POST /api/followup/templates/<id>/rule  — Get or upsert rule for    #
    #  a template (template-centric rule management)                       #
    # ------------------------------------------------------------------ #
    @http.route(
        '/api/followup/templates/<int:template_id>/rule',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def get_template_rule(self, template_id, **kwargs):
        """Get the auto-followup rule linked to this template."""
        err = _require_sales_access()
        if err:
            return err

        Rule = request.env['dke.followup.rule'].sudo()
        rule = Rule.search([('wa_template_id', '=', template_id)], limit=1)
        if not rule:
            return {'status': 'success', 'data': None}
        return {'status': 'success', 'data': _serialize_rule(rule)}

    @http.route(
        '/api/followup/templates/<int:template_id>/rule/save',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def save_template_rule(self, template_id, **kwargs):
        """Create or update auto-followup rule for a template."""
        err = _require_sales_access()
        if err:
            return err

        wa_tpl = request.env['whatsapp.template'].sudo().browse(template_id)
        if not wa_tpl.exists():
            return _error(404, 'Template tidak ditemukan.')

        delay_days = kwargs.get('delay_days')
        is_active = kwargs.get('is_active', True)
        trigger_event = kwargs.get('trigger_event', 'last_day_chat')

        if delay_days is None:
            return _error(400, 'Delay wajib diisi.')
        try:
            delay_days = int(delay_days)
        except (ValueError, TypeError):
            return _error(400, 'Delay harus berupa angka.')
        if delay_days < 0:
            return _error(400, 'Delay tidak boleh negatif.')

        Rule = request.env['dke.followup.rule'].sudo()
        rule = Rule.search([('wa_template_id', '=', template_id)], limit=1)

        try:
            if rule:
                rule.write({
                    'delay_days': delay_days,
                    'is_active': bool(is_active),
                    'trigger_event': trigger_event,
                })
            else:
                rule = Rule.create({
                    'name': 'Auto: %s' % wa_tpl.name,
                    'trigger_event': trigger_event,
                    'delay_days': delay_days,
                    'is_active': bool(is_active),
                    'wa_template_id': template_id,
                    'created_by_id': request.env.uid,
                })
            return {
                'status': 'success',
                'data': _serialize_rule(rule),
                'message': 'Aturan otomatis berhasil disimpan.',
            }
        except ValidationError as e:
            return _error(400, str(e))

    @http.route(
        '/api/followup/templates/<int:template_id>/rule/delete',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def delete_template_rule(self, template_id, **kwargs):
        """Delete the auto-followup rule linked to this template."""
        err = _require_sales_access()
        if err:
            return err

        Rule = request.env['dke.followup.rule'].sudo()
        rule = Rule.search([('wa_template_id', '=', template_id)], limit=1)
        if not rule:
            return _error(404, 'Tidak ada aturan otomatis untuk template ini.')

        # Cancel pending messages
        pending = request.env['dke.scheduled.message'].sudo().search([
            ('followup_rule_id', '=', rule.id),
            ('state', '=', 'pending'),
        ])
        if pending:
            pending.write({'state': 'cancelled'})

        rule.unlink()
        return {'status': 'success', 'message': 'Aturan otomatis berhasil dihapus.'}
