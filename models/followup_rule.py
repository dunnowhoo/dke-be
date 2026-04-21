# -*- coding: utf-8 -*-

import json as _json
import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import ValidationError
from pytz import timezone, utc

_logger = logging.getLogger(__name__)


class FollowUpRule(models.Model):
    """Automated follow-up rules for customer engagement.

    Sales Staff can create rules that automatically send WhatsApp
    template messages to customers X days after their last chat.

    EPIC05 - PBI-32
    """
    _name = 'dke.followup.rule'
    _description = 'Follow-Up Rule'
    _order = 'create_date desc'

    name = fields.Char(string='Rule Name', required=True)

    trigger_event = fields.Selection([
        ('last_day_chat', 'Last Day Chat'),
    ], string='Event Trigger', required=True, default='last_day_chat',
       help='Event yang memicu follow-up. Saat ini hanya mendukung "last day chat".')

    delay_days = fields.Integer(
        string='Delay (Hari)', required=True, default=3,
        help='Jumlah hari setelah trigger event sebelum follow-up dikirim. 0 = langsung kirim di cron berikutnya.',
    )

    is_active = fields.Boolean(string='Aktif', default=True)

    wa_template_id = fields.Many2one(
        'whatsapp.template', string='WhatsApp Template',
        ondelete='set null',
        help='Template WhatsApp (approved oleh Meta) yang digunakan untuk mengirim follow-up. '
             'Wajib diisi karena WhatsApp hanya mengizinkan pengiriman pesan via template '
             'setelah jendela 24 jam berakhir.',
    )

    created_by_id = fields.Many2one(
        'res.users', string='Dibuat Oleh',
        default=lambda self: self.env.uid,
        readonly=True,
    )

    # Track which rooms have already been followed up by this rule
    followup_log_ids = fields.One2many(
        'dke.followup.log', 'rule_id', string='Follow-Up Logs',
    )

    @api.constrains('delay_days')
    def _check_delay_days(self):
        for rec in self:
            if rec.delay_days < 0:
                raise ValidationError('Delay tidak boleh negatif.')

    @api.model
    def cron_execute_followup_rules(self):
        """Cron: run daily at 10:00 WIB (03:00 UTC).

        EPIC05 - PBI-33: For each active rule, find chat rooms where the
        last message was exactly delay_days ago, and no follow-up has been
        sent yet for that rule+room combo. Then create a scheduled message
        (auto_followup) that will be picked up by the scheduled message cron.
        """
        active_rules = self.search([('is_active', '=', True)])
        now = fields.Datetime.now()
        FollowUpLog = self.env['dke.followup.log']
        ScheduledMsg = self.env['dke.scheduled.message']
        ChatRoom = self.env['dke.chat.room']

        for rule in active_rules:
            # Calculate the target date window in WIB (Asia/Jakarta),
            # then convert to UTC for the domain search.
            wib = timezone('Asia/Jakarta')
            now_wib = now.replace(tzinfo=utc).astimezone(wib)
            target_wib = now_wib - timedelta(days=rule.delay_days)
            target_start_wib = target_wib.replace(hour=0, minute=0, second=0, microsecond=0)
            target_end_wib = target_wib.replace(hour=23, minute=59, second=59, microsecond=0)
            # Convert back to naive UTC for Odoo domain
            target_start = target_start_wib.astimezone(utc).replace(tzinfo=None)
            target_end = target_end_wib.astimezone(utc).replace(tzinfo=None)

            # Find rooms with last message in the target window
            rooms = ChatRoom.search([
                ('last_message_time', '>=', target_start),
                ('last_message_time', '<=', target_end),
                ('state', 'in', ['active', 'done']),
            ])

            for room in rooms:
                # Check if already sent for this rule + room
                existing = FollowUpLog.search([
                    ('rule_id', '=', rule.id),
                    ('room_id', '=', room.id),
                ], limit=1)
                if existing:
                    continue

                # Use WA template body as the message content for chat history
                wa_body = rule.wa_template_id.body or rule.wa_template_id.name or ''

                # Auto-populate variable values from customer data so
                # free_text template variables are filled, not demo_value.
                auto_vars = {}
                customer = room.customer_id
                if customer and rule.wa_template_id:
                    body_vars = rule.wa_template_id.variable_ids.filtered(
                        lambda v: v.line_type == 'body' and v.field_type == 'free_text'
                    )
                    for idx, _var in enumerate(sorted(body_vars, key=lambda v: v.name), start=1):
                        if idx == 1:
                            auto_vars[str(idx)] = customer.name or ''
                        else:
                            # Additional free_text vars — use demo_value
                            auto_vars[str(idx)] = _var.demo_value or ''

                try:
                    # Create scheduled message for immediate sending
                    ScheduledMsg.create({
                        'chat_room_id': room.id,
                        'customer_id': room.customer_id.id if room.customer_id else False,
                        'created_by_id': self.env.ref('base.user_root').id,
                        'message': wa_body,
                        'send_at': now,
                        'state': 'pending',
                        'schedule_type': 'auto_followup',
                        'followup_rule_id': rule.id,
                        'wa_template_id': rule.wa_template_id.id if rule.wa_template_id else False,
                        'variable_values': _json.dumps(auto_vars) if auto_vars else '',
                    })

                    # Log — message is queued (pending), not yet sent.
                    # The scheduled-message cron will update actual send state.
                    FollowUpLog.create({
                        'rule_id': rule.id,
                        'room_id': room.id,
                        'customer_id': room.customer_id.id if room.customer_id else False,
                        'message_sent': wa_body,
                        'state': 'pending',
                    })

                    _logger.info(
                        'Follow-up rule "%s" triggered for room "%s"',
                        rule.name, room.name,
                    )

                except Exception as e:
                    _logger.exception(
                        'Failed to create follow-up for rule %s, room %s',
                        rule.id, room.id,
                    )
                    FollowUpLog.create({
                        'rule_id': rule.id,
                        'room_id': room.id,
                        'customer_id': room.customer_id.id if room.customer_id else False,
                        'message_sent': wa_body,
                        'state': 'failed',
                        'error_message': str(e)[:500],
                    })


class FollowUpLog(models.Model):
    """Log of follow-up messages sent by rules to avoid duplicates.

    EPIC05 - PBI-33
    """
    _name = 'dke.followup.log'
    _description = 'Follow-Up Execution Log'
    _order = 'sent_at desc'

    rule_id = fields.Many2one(
        'dke.followup.rule', string='Rule', required=True, ondelete='cascade',
    )
    room_id = fields.Many2one(
        'dke.chat.room', string='Chat Room', required=True, ondelete='cascade',
    )
    customer_id = fields.Many2one('res.partner', string='Customer')
    message_sent = fields.Text(string='Message Sent')
    sent_at = fields.Datetime(string='Sent At', default=fields.Datetime.now)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ], string='Status', default='pending')
    error_message = fields.Text(string='Error')
