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
                wa_sent = self._send_via_whatsapp_template(msg, room)

                # Create chat message in history
                if msg.chat_room_id:
                    room_rec = msg.chat_room_id
                    if room_rec.discuss_channel_id:
                        # Room linked to native WhatsApp discuss.channel
                        from markupsafe import Markup
                        channel = room_rec.discuss_channel_id.sudo()
                        author = msg.created_by_id.partner_id if msg.created_by_id else None
                        channel.message_post(
                            body=Markup('<p>%s</p>') % (msg.message or ''),
                            message_type='whatsapp_message',
                            subtype_xmlid='mail.mt_comment',
                            author_id=author.id if author else None,
                        )
                    else:
                        # session_id=False is set EXPLICITLY so automated follow-up
                        # messages are NEVER linked to a CC session and never affect
                        # session evaluation metrics (first response time, rating, etc.).
                        msg_src = 'followup' if msg.schedule_type == 'auto_followup' else 'promo'
                        self.env['dke.chat.message'].sudo().create({
                            'room_id': msg.chat_room_id.id,
                            'session_id': False,
                            'sender_type': 'system',
                            'sender_id': msg.created_by_id.id if msg.created_by_id else False,
                            'content_text': msg.message,
                            'message_type': 'text',
                            'is_automated': True,
                            'send_status': 'sent' if wa_sent else 'failed',
                            'message_source': msg_src,
                            'created_at': now,
                        })
                    # Update room last_message_time
                    room_rec.write({'last_message_time': now})

                new_state = 'sent' if wa_sent else 'failed'
                msg.write({
                    'state': new_state,
                    'sent_at': now,
                    'error_message': False if wa_sent else 'WhatsApp API send failed — no active account, phone, or template',
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

        Uses the native whatsapp.message flow:
        1. Resolve the WhatsApp template (from followup_rule or fallback)
        2. Create a mail.message record (required by whatsapp.message)
        3. Create a whatsapp.message record in 'outgoing' state
        4. Call _send() which routes to the WhatsApp Cloud API

        Supports text, image, video, document headers — whatever the
        approved template defines.

        Returns True if the message was queued/sent, False otherwise.
        """
        try:
            # --- 1. Resolve WhatsApp account ---
            WaAccount = self.env['whatsapp.account'].sudo()
            account = WaAccount.search([('active', '=', True)], limit=1)
            if not account:
                _logger.warning('No active WhatsApp account configured')
                return False

            # --- 2. Resolve phone number ---
            phone = getattr(room, 'external_conversation_id', '') or ''
            if not phone:
                customer = msg.customer_id or getattr(room, 'customer_id', None)
                if customer:
                    phone = customer.mobile or customer.phone or ''
            if not phone:
                _logger.warning('No phone number for room %s', room.name)
                return False

            # Normalize to international format (+countrycode) so Odoo's
            # mobile_number_formatted computed field can parse it reliably.
            phone = phone.strip()
            if phone.startswith('0'):
                phone = '+62' + phone[1:]  # Indonesian local → international
            elif phone.isdigit() and len(phone) >= 10:
                phone = '+' + phone  # Bare digits → prepend +

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
                return False

            # --- 4. Build mail.message for tracking ---
            customer_partner = msg.customer_id or getattr(room, 'customer_id', None)
            mail_vals = {
                'model': 'res.partner',
                'res_id': customer_partner.id if customer_partner else 0,
                'body': msg.message or '',
                'message_type': 'whatsapp_message',
                'subtype_id': self.env.ref('mail.mt_note').id,
            }
            # Link the customer partner so Odoo can detect their country
            # for phone number formatting in mobile_number_formatted.
            if customer_partner:
                mail_vals['partner_ids'] = [Command.link(customer_partner.id)]
            mail_msg = self.env['mail.message'].sudo().create(mail_vals)

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
            wa_msg.invalidate_recordset(['state', 'failure_reason'])
            if wa_msg.state == 'error':
                _logger.warning(
                    'WhatsApp Cloud API rejected message for room %s: %s',
                    room.name, wa_msg.failure_reason or 'unknown',
                )
                return False

            _logger.info(
                'WhatsApp template "%s" sent for room %s (phone: %s)',
                wa_template.template_name, room.name, phone,
            )
            return True

        except Exception as e:
            _logger.warning('WhatsApp send failed for room %s: %s', room.name, e)
            return False
