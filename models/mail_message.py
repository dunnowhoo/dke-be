# -*- coding: utf-8 -*-

import json
import logging
import re
import threading

import requests as _requests

from odoo import models, api
from odoo.fields import Command

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    """Extend mail.message to push bus notifications when WhatsApp
    messages arrive on discuss.channel records linked to dke.chat.room.

    This removes the need for frontend polling — the bus long-poll
    delivers new-message events in real time.
    """
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        self._notify_dke_chat_rooms(messages)
        return messages

    def _notify_dke_chat_rooms(self, messages):
        """For each new mail.message on a WhatsApp discuss.channel that
        is linked to a dke.chat.room, push a bus.bus notification so
        the frontend receives it instantly.

        When a customer replies for the first time after a promo blast, Odoo
        native WA creates a NEW discuss.channel (ch2) while the room still
        points to the old one (ch1).  We handle that by falling back to a
        phone/partner lookup so we can update discuss_channel_id on-the-fly
        and notify immediately — no need to wait for the next /api/chat/list poll.
        """
        Room = self.env['dke.chat.room'].sudo()
        Channel = self.env['discuss.channel'].sudo()

        # Collect channel IDs from relevant messages
        channel_msg_map = {}
        for msg in messages:
            if (msg.model == 'discuss.channel'
                    and msg.message_type in ('comment', 'whatsapp_message')
                    and msg.res_id):
                channel_msg_map.setdefault(msg.res_id, []).append(msg)

        if not channel_msg_map:
            return

        # Find rooms already linked to these channels
        rooms = Room.search([
            ('discuss_channel_id', 'in', list(channel_msg_map.keys())),
        ])
        room_by_channel = {r.discuss_channel_id.id: r for r in rooms}

        # For channels not yet linked to any room, try to match by phone/partner
        # (e.g. customer replies on a new channel after a promo blast)
        unlinked_channel_ids = set(channel_msg_map.keys()) - set(room_by_channel.keys())
        for channel_id in unlinked_channel_ids:
            channel = Channel.browse(channel_id)
            if not channel.exists() or channel.channel_type != 'whatsapp':
                continue
            phone = channel.whatsapp_number or ''
            partner = channel.whatsapp_partner_id

            # Only process if there is actually a customer message on this channel
            has_customer_msg = any(
                m.author_id == partner
                for msg_list in [channel_msg_map[channel_id]]
                for m in msg_list
                if partner
            )
            if not has_customer_msg:
                continue

            # Find room by phone or partner
            domain = [('source', '=', 'whatsapp')]
            if phone:
                domain.append(('external_conversation_id', '=', phone))
            elif partner:
                domain.append(('customer_id', '=', partner.id))
            else:
                continue

            room = Room.search(domain, limit=1)
            if not room:
                continue

            # Update discuss_channel_id so messages endpoint reads from correct channel
            update_vals = {
                'discuss_channel_id': channel_id,
                'state': 'active',
            }
            latest_msg = channel_msg_map[channel_id][-1]
            if not room.last_message_time or latest_msg.create_date > room.last_message_time:
                update_vals['last_message_time'] = latest_msg.create_date
            room.write(update_vals)
            # Ensure admin partner can see the new/updated channel in Discuss.
            # Odoo native WA uses Command.clear() when setting channel_member_ids,
            # wiping admin's membership. Re-add it here after switching channels.
            try:
                channel_obj = self.env['discuss.channel'].sudo().browse(channel_id)
                admin_partner = self.env.ref('base.partner_admin').sudo()
                already = channel_obj.channel_member_ids.filtered(
                    lambda m: m.partner_id == admin_partner
                )
                if not already:
                    channel_obj.write({
                        'channel_member_ids': [Command.create({'partner_id': admin_partner.id})],
                    })
            except Exception:
                _logger.debug('ensure_admin_member failed for channel %s', channel_id, exc_info=True)
            room_by_channel[channel_id] = room
            _logger.info(
                'mail_message: updated discuss_channel_id on room %s → channel %s (customer reply)',
                room.id, channel_id,
            )

        if not room_by_channel:
            return

        for channel_id, msgs in channel_msg_map.items():
            room = room_by_channel.get(channel_id)
            if not room:
                continue

            channel = Channel.browse(channel_id)
            if not channel.exists():
                continue

            for mail_msg in msgs:
                try:
                    msg_dict = self._format_msg_for_bus(mail_msg, channel, room)

                    # Notify the specific room channel
                    self.env['bus.bus']._sendone(
                        'dke_chat_room_%s' % room.id,
                        'chat.new_message',
                        {'room_id': room.id, 'message': msg_dict},
                    )

                    # Also notify the global channel so room list refreshes
                    self.env['bus.bus']._sendone(
                        'dke_chat_available',
                        'chat.new_message',
                        {'room_id': room.id},
                    )
                except Exception:
                    _logger.debug(
                        'bus notification failed for room %s on mail.message create',
                        room.id, exc_info=True,
                    )

        # Call FE webhook for all rooms that received new messages
        self._call_fe_webhook_batch(list(room_by_channel.values()), channel_msg_map)

    def _call_fe_webhook_batch(self, rooms, channel_msg_map):
        """Fire-and-forget HTTP POST to the FE webhook URL for each room.

        The URL and optional secret are stored in ir.config_parameter:
          dke.chat.webhook_url    — e.g. https://propenheimer.vercel.app/api/chat/webhook
          dke.chat.webhook_secret — value sent as x-webhook-secret header (optional)
        """
        ICP = self.env['ir.config_parameter'].sudo()
        webhook_url = ICP.get_param('dke.chat.webhook_url', '')
        if not webhook_url:
            return  # not configured — skip silently

        webhook_secret = ICP.get_param('dke.chat.webhook_secret', '')
        headers = {'Content-Type': 'application/json'}
        if webhook_secret:
            headers['x-webhook-secret'] = webhook_secret

        # Build one payload per room and fire in a background thread
        payloads = []
        for room in rooms:
            payloads.append({
                'room_id': room.id,
                'event': 'chat.new_message',
            })

        def _post():
            for payload in payloads:
                try:
                    _requests.post(
                        webhook_url,
                        data=json.dumps(payload),
                        headers=headers,
                        timeout=5,
                    )
                except Exception:
                    _logger.debug(
                        'FE webhook call failed for room %s',
                        payload.get('room_id'), exc_info=True,
                    )

        t = threading.Thread(target=_post, daemon=True)
        t.start()

    @staticmethod
    def _format_msg_for_bus(mail_msg, channel, room):
        """Minimal message dict for bus notification payload."""
        is_customer = (
            channel.whatsapp_partner_id
            and mail_msg.author_id == channel.whatsapp_partner_id
        )
        sender_type = 'customer' if is_customer else 'cs'

        body = mail_msg.body or ''
        plain = re.sub(r'<[^>]+>', '', body).strip()

        att = mail_msg.attachment_ids[:1] if mail_msg.attachment_ids else None
        att_url = ''
        att_filename = ''
        msg_type = 'text'
        if att:
            if not att.access_token:
                att.sudo().generate_access_token()
            token = att.access_token or ''
            att_url = '/web/content/%d?download=true' % att.id
            if token:
                att_url += '&access_token=%s' % token
            att_filename = att.name or ''
            mimetype = att.mimetype or ''
            msg_type = 'image' if mimetype.startswith('image/') else 'file'

        created_at = ''
        if mail_msg.create_date:
            created_at = mail_msg.create_date.strftime('%Y-%m-%d %H:%M:%S')

        return {
            'id': mail_msg.id,
            'room_id': room.id,
            'session_id': None,
            'external_message_id': '',
            'sender_type': sender_type,
            'sender_id': (mail_msg.author_id.user_ids[:1].id
                          if mail_msg.author_id and mail_msg.author_id.user_ids
                          else None),
            'agent_name': mail_msg.author_id.name if mail_msg.author_id else None,
            'content_text': plain,
            'message_type': msg_type,
            'attachment_url': att_url,
            'attachment_filename': att_filename,
            'is_read': False,
            'is_automated': False,
            'send_status': 'pending' if sender_type == 'cs' else 'sent',
            'created_at': created_at,
        }
