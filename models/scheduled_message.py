# -*- coding: utf-8 -*-

import logging

from odoo import models, fields, api, Command

_logger = logging.getLogger(__name__)


class ScheduledMessage(models.Model):
    """Scheduled follow-up messages.

    Supports both automated follow-ups (Cron-based, EPIC05 PBI-33)
    and manually scheduled messages (EPIC05 PBI-34).

    EPIC05 - PBI-33, PBI-34
    """
    _name = 'dke.scheduled.message'
    _description = 'Scheduled Message'
    _order = 'send_at asc'

    # Room — supports both chat rooms and ticketing rooms
    chat_room_id = fields.Many2one(
        'dke.chat.room', string='Chat Room', ondelete='cascade',
    )
    room_id = fields.Many2one(
        'dke.ticketing.room', string='Ticketing Room', ondelete='cascade',
    )
    customer_id = fields.Many2one('res.partner', string='Customer')
    created_by_id = fields.Many2one('res.users', string='Created By')

    # Content
    message = fields.Text(string='Message Content', required=True)

    # Schedule
    send_at = fields.Datetime(string='Scheduled Send Time', required=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('sent', 'Sent'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ], string='Status', default='pending')

    # Type
    schedule_type = fields.Selection([
        ('auto_followup', 'Auto Follow-Up (System)'),
        ('manual', 'Manual Schedule'),
    ], string='Schedule Type', default='manual')

    # Reference
    followup_rule_id = fields.Many2one(
        'dke.followup.rule', string='Follow-Up Rule', ondelete='set null',
    )
    wa_template_id = fields.Many2one(
        'whatsapp.template', string='WhatsApp Template', ondelete='set null',
    )
    sale_order_id = fields.Many2one('sale.order', string='Related Sale Order')
    variable_values = fields.Text(string='Variable Values JSON',
        help='JSON dict of variable values, e.g. {"1": "Agus", "2": "ORD-001"}')
    sent_at = fields.Datetime(string='Actually Sent At')
    error_message = fields.Text(string='Error Message')

    @api.model
    def cron_send_scheduled_messages(self):
        """Cron: run every minute. Send pending messages where send_at <= now.

        EPIC05 - PBI-34: Worker sends pending scheduled messages via
        WhatsApp template, then inserts into chat history.

        Race-condition guard: we atomically claim each pending message by
        flipping state → 'processing' via a direct UPDATE WHERE state='pending'.
        This prevents multiple cron workers from double-sending the same message
        when Odoo runs the cron across several parallel worker processes.
        """
        now = fields.Datetime.now()
        pending = self.search([
            ('state', '=', 'pending'),
            ('send_at', '<=', now),
        ])
        if not pending:
            return

        # Atomically claim all found messages: only those still 'pending' will
        # be updated (handles TOCTOU if another worker races us).
        self.env.cr.execute(
            """
            UPDATE dke_scheduled_message
               SET state = 'processing'
             WHERE id = ANY(%s)
               AND state = 'pending'
            RETURNING id
            """,
            (list(pending.ids),),
        )
        claimed_ids = [row[0] for row in self.env.cr.fetchall()]
        if not claimed_ids:
            return  # Another worker claimed them all first
        pending = self.browse(claimed_ids)

        for msg in pending:
            try:
                room = msg.chat_room_id or msg.room_id
                if not room:
                    msg.write({'state': 'failed', 'error_message': 'No room linked'})
                    continue

                # Try to send via WhatsApp template
                wa_sent, wa_err = self._send_via_whatsapp_template(msg, room)

                # Create chat message in history.
                # ALWAYS use dke.chat.message — NEVER channel.message_post.
                # channel.message_post creates a mail.message which the frontend
                # cannot read (it only reads dke.chat.message). Using it for rooms
                # with discuss_channel_id causes the bubble to appear blue (CS agent)
                # instead of yellow "AUTO FOLLOW-UP".
                # session_id=False ensures this is NEVER linked to a CC session.
                if msg.chat_room_id:
                    room_rec = msg.chat_room_id
                    # All automated/scheduled messages use 'followup' source so
                    # they render as amber "AUTO FOLLOW-UP" on the frontend.
                    # 'promo' (green) is reserved for marketing campaign blasts
                    # which go through a separate flow.
                    self.env['dke.chat.message'].sudo().create({
                        'room_id': room_rec.id,
                        'session_id': False,
                        'sender_type': 'system',
                        'sender_id': msg.created_by_id.id if msg.created_by_id else False,
                        'content_text': msg.message,
                        'message_type': 'text',
                        'is_automated': True,
                        'send_status': 'sent' if wa_sent else 'failed',
                        'message_source': 'followup',
                        'created_at': now,
                    })
                    room_rec.write({'last_message_time': now})

                new_state = 'sent' if wa_sent else 'failed'
                msg.write({
                    'state': new_state,
                    'sent_at': now,
                    'error_message': False if wa_sent else (wa_err or 'WhatsApp API send failed'),
                })

                # Update the FollowUpLog for this message so the logs endpoint
                # shows accurate state instead of staying 'pending' forever.
                if msg.followup_rule_id:
                    log = self.env['dke.followup.log'].sudo().search([
                        ('rule_id', '=', msg.followup_rule_id.id),
                        ('room_id', '=', (msg.chat_room_id or msg.room_id).id),
                        ('state', '=', 'pending'),
                    ], limit=1)
                    if log:
                        log.write({'state': new_state})

                if wa_sent:
                    _logger.info(
                        'Scheduled message %d sent for room %s',
                        msg.id, room.name,
                    )
                else:
                    _logger.warning(
                        'Scheduled message %d WA API failed for room %s (recorded in chat)',
                        msg.id, room.name,
                    )

            except Exception as e:
                _logger.exception('Failed to send scheduled message %d', msg.id)
                msg.write({
                    'state': 'failed',
                    'error_message': str(e)[:500],
                })

    def _send_via_whatsapp_template(self, msg, room):
        """Send message via Odoo WhatsApp template (whatsapp module).

        Returns (True, '') on success or (False, '<reason>') on failure so
        callers can surface the exact failure in the API response / log.
        """
        try:
            # --- 1. Resolve WhatsApp account ---
            WaAccount = self.env['whatsapp.account'].sudo()
            account = WaAccount.search([('active', '=', True)], limit=1)
            if not account:
                _logger.warning('No active WhatsApp account configured')
                return False, 'No active WhatsApp account configured'

            # --- 2. Resolve phone number ---
            phone = getattr(room, 'external_conversation_id', '') or ''
            if not phone:
                customer = msg.customer_id or getattr(room, 'customer_id', None)
                if customer:
                    phone = customer.mobile or customer.phone or ''
            if not phone:
                _logger.warning('No phone number for room %s', room.name)
                return False, 'No phone number for room %s' % room.name

            # Normalize to international format (+countrycode) so Odoo's
            # mobile_number_formatted computed field can parse it reliably.
            # Strip spaces, dashes, dots, parentheses — res.partner stores phone
            # in many human-readable formats like '+62 812-5581-2675'.
            had_plus = phone.strip().startswith('+')
            phone_digits = ''.join(c for c in phone if c.isdigit())
            if not phone_digits:
                _logger.warning('No phone number for room %s after normalization', room.name)
                return False
            if phone_digits.startswith('0'):
                phone = '+62' + phone_digits[1:]   # 08xxx → +628xxx
            elif had_plus or phone_digits.startswith('62'):
                phone = '+' + phone_digits          # +62xxx or 62xxx → +62xxx
            else:
                phone = '+62' + phone_digits        # bare 8xxx → +628xxx

            # --- 3. Resolve WhatsApp template ---
            wa_template = None
            # Priority 1: template explicitly linked to this message
            if msg.wa_template_id:
                wa_template = msg.wa_template_id
            # Priority 2: template from the followup rule
            if not wa_template and msg.followup_rule_id and msg.followup_rule_id.wa_template_id:
                wa_template = msg.followup_rule_id.wa_template_id
            # Priority 3: fallback to any approved template on account
            if not wa_template:
                wa_template = self.env['whatsapp.template'].sudo().search([
                    ('status', '=', 'approved'),
                    ('wa_account_id', '=', account.id),
                ], limit=1)
            if not wa_template:
                _logger.warning('No approved WhatsApp template found for account %s', account.name)
                return False, 'No approved WhatsApp template found for account %s' % account.name

            # --- 4. Build mail.message for tracking ---
            # CRITICAL: mail_message_id.model MUST equal wa_template.model.
            # Odoo's whatsapp.message._send_message() enforces:
            #   if mail_msg.model != wa_template.model → WhatsAppError(failure_type='template')
            # wa_template.model has default='res.partner' (required field), so for all
            # templates we create this is always 'res.partner'. Mirror it here exactly.
            customer_partner = msg.customer_id or getattr(room, 'customer_id', None)
            template_model = wa_template.model or 'res.partner'

            # Ensure template has model_id set (guard for templates imported without it).
            if not wa_template.model_id:
                IrModel = self.env['ir.model'].sudo()
                partner_model = IrModel.search([('model', '=', 'res.partner')], limit=1)
                if partner_model:
                    wa_template.sudo().write({'model_id': partner_model.id})
                    wa_template.invalidate_recordset(['model'])
                    template_model = 'res.partner'

            # Resolve res_id for the template model.
            mail_res_id = 0
            if template_model == 'res.partner' and customer_partner:
                mail_res_id = customer_partner.id

            mail_vals = {
                'model': template_model,
                'res_id': mail_res_id,
                'body': msg.message or '',
                'message_type': 'whatsapp_message',
                'subtype_id': self.env.ref('mail.mt_note').id,
            }
            # Link the customer partner so Odoo can detect their country for
            # phone number formatting in mobile_number_formatted (computed field).
            if customer_partner:
                mail_vals['partner_ids'] = [Command.link(customer_partner.id)]
            mail_msg = self.env['mail.message'].sudo().create(mail_vals)

            # Flush the ORM write queue so the M2M partner_ids relation is
            # persisted to DB BEFORE whatsapp.message is created. The
            # _compute_mobile_number_formatted computed field on whatsapp.message
            # reads mail_message_id.partner_ids[0].country_id for phone validation;
            # without a flush the partner link may not yet be visible.
            self.env.cr.flush()

            # --- 5. Build free_text_json from variable_values ---
            import json as _json
            free_text_json = {}
            raw_vars = getattr(msg, 'variable_values', None) or ''
            if raw_vars:
                try:
                    parsed = _json.loads(raw_vars) if isinstance(raw_vars, str) else raw_vars
                    if isinstance(parsed, dict):
                        # Convert {"1": "val", "2": "val"} → {"free_text_1": "val", ...}
                        idx = 1
                        for key in sorted(parsed.keys(), key=lambda k: int(k) if k.isdigit() else k):
                            free_text_json['free_text_%d' % idx] = str(parsed[key])
                            idx += 1
                except Exception:
                    pass

            # --- 6. Create whatsapp.message (outgoing) ---
            wa_vals = {
                'mail_message_id': mail_msg.id,
                'mobile_number': phone,
                'wa_template_id': wa_template.id,
                'wa_account_id': account.id,
            }
            WaMsg = self.env['whatsapp.message'].sudo()
            if 'free_text_json' in WaMsg._fields:
                wa_vals['free_text_json'] = free_text_json
            wa_msg = WaMsg.create(wa_vals)

            # --- 7. Send (single message → immediate) ---
            wa_msg._send()

            # Check actual send result — _send() doesn't raise on error,
            # it writes state='error' on the whatsapp.message record.
            wa_msg.invalidate_recordset(['state', 'failure_type', 'failure_reason'])
            if wa_msg.state == 'error':
                reason = '%s: %s' % (wa_msg.failure_type or 'unknown', wa_msg.failure_reason or '(none)')
                _logger.warning(
                    'WhatsApp Cloud API rejected message for room %s: %s',
                    room.name, reason,
                )
                return False, reason

            _logger.info(
                'WhatsApp template "%s" sent for room %s (phone: %s)',
                wa_template.template_name, room.name, phone,
            )
            return True, ''

        except Exception as e:
            _logger.warning('WhatsApp send failed for room %s: %s', room.name, e)
            return False, str(e)
