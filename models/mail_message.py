# -*- coding: utf-8 -*-

import logging
import re

from odoo import models, api

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
        the frontend receives it instantly."""
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

        # Find rooms linked to these channels
        rooms = Room.search([
            ('discuss_channel_id', 'in', list(channel_msg_map.keys())),
        ])
        if not rooms:
            return

        room_by_channel = {r.discuss_channel_id.id: r for r in rooms}

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
            'send_status': 'sent',
            'created_at': created_at,
        }
