# -*- coding: utf-8 -*-

import base64
import json
import logging
import re

from odoo import http, fields, Command
from odoo.http import request

_logger = logging.getLogger(__name__)


class ChatController(http.Controller):
    """REST API endpoints for Chat system.

    EPIC02 - PBI-9  : Melihat Daftar Semua Chat dari WhatsApp
    EPIC02 - PBI-20 : Melihat Detail Riwayat Percakapan Pelanggan
    EPIC02 - PBI-21 : Mengirim Pesan Balasan ke Pelanggan
    EPIC02 - PBI-22 : Mengakhiri Sesi Chat dengan Pelanggan
    EPIC05 - PBI-34 : Menjadwalkan Pesan Follow-Up Manual

    Data disimpan permanen di dke.chat.room & dke.chat.message,
    BUKAN di Odoo Discuss (mail.message) agar tidak hilang saat
    session WhatsApp addon expired/disconnect.
    """

    # ──────────────────────────────────────────────────────────────
    # Helper
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_dt(dt):
        """Return ISO-8601 string with Z suffix (Odoo stores UTC) or None."""
        if not dt:
            return None
        s = fields.Datetime.to_string(dt)
        # Append 'Z' so JS Date() treats it as UTC
        return s + 'Z' if s and not s.endswith('Z') else s

    @staticmethod
    def _room_to_dict(room, include_preview=False):
        data = {
            'id': room.id,
            'name': room.name,
            'customer_name': room.customer_name or '',
            'customer_phone': room.external_conversation_id or '',
            'customer_id': room.customer_id.id if room.customer_id else None,
            'source': room.source,
            'state': room.state,
            'is_assigned': room.is_assigned,
            'assigned_to': room.assigned_to.name if room.assigned_to else None,
            'assigned_cs': room.assigned_to.name if room.assigned_to else None,
            'assigned_cs_id': room.assigned_to.id if room.assigned_to else None,
            'assigned_at': ChatController._fmt_dt(room.assigned_at),
            'unread_count': room.unread_count,
            'last_message_time': ChatController._fmt_dt(room.last_message_time),
            'has_discuss_channel': bool(room.discuss_channel_id),
        }
        if include_preview:
            messages = room.message_ids
            data['message_count'] = len(messages)
            last_msg = messages[-1] if messages else None
            preview = ''
            if last_msg and last_msg.content_text:
                preview = last_msg.content_text[:50]
            data['preview_message'] = preview
            data['created_at'] = ChatController._fmt_dt(
                messages[0].created_at if messages else room.create_date
            )
        return data

    @staticmethod
    def _message_to_dict(msg):
        # Map backend 'admin' sender_type to frontend 'cs'
        sender_type = msg.sender_type
        if sender_type == 'admin':
            sender_type = 'cs'

        # Resolve original filename from ir.attachment when available
        att_filename = ''
        att_url = msg.attachment_url or ''
        if att_url and '/web/content/' in att_url:
            try:
                att_id_str = att_url.split('/web/content/')[1].split('?')[0]
                att_rec = msg.env['ir.attachment'].sudo().browse(int(att_id_str))
                if att_rec.exists():
                    att_filename = att_rec.name or ''
            except Exception:
                pass
        if not att_filename and msg.message_type in ('image', 'file'):
            att_filename = msg.content_text or ''

        return {
            'id': msg.id,
            'room_id': msg.room_id.id if msg.room_id else None,
            'external_message_id': msg.external_message_id or '',
            'sender_type': sender_type,
            'sender_id': msg.sender_id.id if msg.sender_id else None,
            'agent_name': msg.sender_id.name if msg.sender_id else None,
            'content_text': msg.content_text or '',
            'message_type': msg.message_type,
            'attachment_url': att_url,
            'attachment_filename': att_filename,
            'is_read': msg.is_read,
            'is_automated': msg.is_automated,
            'send_status': msg.send_status,
            'created_at': ChatController._fmt_dt(msg.created_at),
        }

    @staticmethod
    def _discuss_msg_to_dict(mail_msg, channel):
        """Convert a mail.message from a discuss.channel into the same
        shape as _message_to_dict so the frontend can render it uniformly."""
        # Determine sender_type: if author == whatsapp_partner → customer
        is_customer = (
            channel.whatsapp_partner_id
            and mail_msg.author_id == channel.whatsapp_partner_id
        )
        sender_type = 'customer' if is_customer else 'cs'

        # Strip HTML tags for plain-text content
        body = mail_msg.body or ''
        plain = re.sub(r'<[^>]+>', '', body).strip()

        # Check for attachments
        att = mail_msg.attachment_ids[:1] if mail_msg.attachment_ids else None
        att_url = ''
        att_filename = ''
        msg_type = 'text'
        if att:
            att_url = '/web/content/%d?download=true' % att.id
            att_filename = att.name or ''
            mimetype = att.mimetype or ''
            msg_type = 'image' if mimetype.startswith('image/') else 'file'

        return {
            'id': mail_msg.id,
            'room_id': channel.id if channel else None,
            'external_message_id': '',
            'sender_type': sender_type,
            'sender_id': mail_msg.author_id.user_ids[:1].id if mail_msg.author_id and mail_msg.author_id.user_ids else None,
            'agent_name': mail_msg.author_id.name if mail_msg.author_id else None,
            'content_text': plain,
            'message_type': msg_type,
            'attachment_url': att_url,
            'attachment_filename': att_filename,
            'is_read': True,
            'is_automated': False,
            'send_status': 'sent',
            'created_at': ChatController._fmt_dt(mail_msg.create_date),
        }

    # ──────────────────────────────────────────────────────────────
    # Sync: discuss.channel (WhatsApp) → dke.chat.room
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _sync_whatsapp_channels():
        """Pull all WhatsApp discuss.channel records and ensure each one
        has a corresponding dke.chat.room.  Uses sudo() so non-admin
        Customer-Care users can see all channels.

        Returns the set of newly created dke.chat.room ids (may be empty).
        """
        Channel = request.env['discuss.channel'].sudo()
        Room = request.env['dke.chat.room'].sudo()

        # All WA channels in Discuss
        wa_channels = Channel.search([('channel_type', '=', 'whatsapp')])
        if not wa_channels:
            return set()

        # Rooms already linked
        existing = Room.search([
            ('discuss_channel_id', 'in', wa_channels.ids),
        ])
        linked_ids = {r.discuss_channel_id.id for r in existing}

        new_ids = set()
        for ch in wa_channels:
            if ch.id in linked_ids:
                # Refresh last_message_time from the channel's latest message
                last_msg = request.env['mail.message'].sudo().search(
                    [('model', '=', 'discuss.channel'),
                     ('res_id', '=', ch.id),
                     ('message_type', 'in', ('comment', 'whatsapp_message'))],
                    order='create_date desc', limit=1,
                )
                if last_msg:
                    room = existing.filtered(lambda r: r.discuss_channel_id.id == ch.id)[:1]
                    if room and (not room.last_message_time or last_msg.create_date > room.last_message_time):
                        updates = {'last_message_time': last_msg.create_date}
                        # Re-open closed room when a new customer message arrives
                        if room.state == 'done':
                            is_customer_msg = (
                                ch.whatsapp_partner_id
                                and last_msg.author_id == ch.whatsapp_partner_id
                            )
                            if is_customer_msg:
                                updates.update({
                                    'state': 'active',
                                    'is_assigned': False,
                                    'assigned_to': False,
                                    'assigned_at': False,
                                })
                        room.write(updates)

                        # Notify via bus so the FE picks up new messages
                        # in real-time without polling.
                        try:
                            msg_dict = ChatController._discuss_msg_to_dict(
                                last_msg, ch
                            )
                            request.env['bus.bus']._sendone(
                                'dke_chat_room_%s' % room.id,
                                'chat.new_message',
                                {'room_id': room.id, 'message': msg_dict},
                            )
                        except Exception:
                            _logger.debug(
                                'bus notification failed during sync for room %s',
                                room.id, exc_info=True,
                            )
                continue

            # Resolve customer partner name / phone
            partner = ch.whatsapp_partner_id
            phone = ch.whatsapp_number or ''
            customer_name = partner.name if partner else phone

            # Determine last message time
            last_msg = request.env['mail.message'].sudo().search(
                [('model', '=', 'discuss.channel'),
                 ('res_id', '=', ch.id),
                 ('message_type', 'in', ('comment', 'whatsapp_message'))],
                order='create_date desc', limit=1,
            )

            room = Room.create({
                'name': ch.name or ('WA: %s' % customer_name),
                'customer_name': customer_name,
                'customer_id': partner.id if partner else False,
                'external_conversation_id': phone,
                'source': 'whatsapp',
                'state': 'active',
                'is_assigned': False,
                'discuss_channel_id': ch.id,
                'last_message_time': last_msg.create_date if last_msg else ch.create_date,
            })
            new_ids.add(room.id)

        return new_ids

    # ──────────────────────────────────────────────────────────────
    # PBI-2: List chat rooms
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/list', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_chat_list(self, **kwargs):
        """GET /api/chat/list — List all chat rooms with pagination.

        EPIC02 - PBI-9: Returns conversations sorted by last_message_time desc.
        Query Params:
          - page   : int (default 1)
          - limit  : int (default 20)
          - source : 'whatsapp' | 'shopee' | 'platform' (optional)
          - state  : 'active' | 'done' | 'archived' (optional)
          - search : string — cari customer_name / phone (optional)
        """
        try:
            # Sync native WA discuss channels → dke.chat.room
            self._sync_whatsapp_channels()

            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)
            source = kwargs.get('source')
            state = kwargs.get('state')
            search = kwargs.get('search', '').strip()

            domain = []
            if source:
                domain.append(('source', '=', source))
            if state:
                domain.append(('state', '=', state))
            if search:
                domain += ['|',
                           ('customer_name', 'ilike', search),
                           ('external_conversation_id', 'ilike', search)]

            Room = request.env['dke.chat.room'].sudo()
            total = Room.search_count(domain)
            rooms = Room.search(domain, limit=limit, offset=(page - 1) * limit)

            return request.make_json_response({
                'status': 'success',
                'meta': {
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': -(-total // limit),  # ceiling division
                },
                'data': [self._room_to_dict(r) for r in rooms],
            })
        except Exception as e:
            _logger.error("get_chat_list error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-3: Message history
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/rooms/<int:room_id>/messages', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_messages(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}/messages — Get full message history.

        EPIC02 - PBI-20: Returns all messages, marks unread messages as read,
                         resets unread_count on the room.
        Query Params:
          - page     : int (default 1)
          - limit    : int (default 50)
          - after_id : int (optional) — return only messages with id > after_id
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 50)), 200)
            after_id = int(kwargs.get('after_id', 0))

            # If the room is linked to a native discuss.channel,
            # read messages from mail.message instead of dke.chat.message.
            if room.discuss_channel_id:
                MailMsg = request.env['mail.message'].sudo()
                msg_domain = [
                    ('model', '=', 'discuss.channel'),
                    ('res_id', '=', room.discuss_channel_id.id),
                    ('message_type', 'in', ('comment', 'whatsapp_message')),
                ]
                if after_id:
                    msg_domain.append(('id', '>', after_id))
                total = MailMsg.search_count(msg_domain)
                mail_msgs = MailMsg.search(
                    msg_domain, limit=limit,
                    offset=(page - 1) * limit if not after_id else 0,
                    order='create_date asc',
                )
                data = [
                    self._discuss_msg_to_dict(m, room.discuss_channel_id)
                    for m in mail_msgs
                ]
            else:
                Msg = request.env['dke.chat.message'].sudo()
                domain = [('room_id', '=', room_id)]
                if after_id:
                    domain.append(('id', '>', after_id))
                total = Msg.search_count(domain)
                messages = Msg.search(
                    domain, limit=limit,
                    offset=(page - 1) * limit if not after_id else 0,
                )

                # Mark unread customer messages as read
                unread = messages.filtered(lambda m: not m.is_read and m.sender_type == 'customer')
                if unread:
                    unread.write({'is_read': True})
                    room.write({'unread_count': max(room.unread_count - len(unread), 0)})

                data = [self._message_to_dict(m) for m in messages]

            return request.make_json_response({
                'status': 'success',
                'room': self._room_to_dict(room),
                'meta': {
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': -(-total // limit) if total else 0,
                },
                'data': data,
            })
        except Exception as e:
            _logger.error("get_room_messages error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-4: Reply to chat
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/rooms/<int:room_id>/reply', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reply_to_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/reply — Send reply from staff.

        EPIC02 - PBI-21: Saves admin reply.  If the room is linked to a
        native WhatsApp discuss.channel, the reply is posted there using
        message_type='whatsapp_message' which triggers Odoo's built-in
        WhatsApp send flow.  Otherwise, it falls back to dke.chat.message.
        Body (JSON): { "message": "...", "type": "text" }
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            # Only the assigned CC can reply
            current_uid = request.env.user.id
            assigned_uid = room.assigned_to.id if room.assigned_to else None
            if room.is_assigned and assigned_uid and assigned_uid != current_uid:
                _logger.info(
                    'Reply blocked: room %s assigned to uid=%s (%s), '
                    'but request comes from uid=%s (%s)',
                    room_id, assigned_uid,
                    room.assigned_to.name,
                    current_uid,
                    request.env.user.name,
                )
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat ini ditangani oleh %s.' % room.assigned_to.name},
                    status=403,
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            message_text = (body.get('message') or '').strip()
            message_type = body.get('type', 'text')

            if not message_text:
                return request.make_json_response(
                    {'status': 'error', 'message': 'message tidak boleh kosong.'}, status=400
                )

            if message_type not in ('text', 'image', 'file'):
                message_type = 'text'

            now = fields.Datetime.now()

            # ── Linked to native WhatsApp discuss.channel ───────
            if room.discuss_channel_id:
                from markupsafe import Markup
                channel = room.discuss_channel_id.sudo()
                new_mail_msg = channel.message_post(
                    body=Markup('<p>%s</p>') % message_text,
                    message_type='whatsapp_message',
                    subtype_xmlid='mail.mt_comment',
                    author_id=request.env.user.partner_id.id,
                )
                room.write({'last_message_time': now})

                msg_dict = self._discuss_msg_to_dict(new_mail_msg, channel)
                self._notify_new_message(room_id, msg_dict)
                return request.make_json_response({
                    'status': 'success',
                    'data': msg_dict,
                })

            # ── Fallback: legacy dke.chat.message ───────────────
            msg = request.env['dke.chat.message'].sudo().create({
                'room_id': room_id,
                'sender_type': 'admin',
                'sender_id': request.env.user.id,
                'content_text': message_text,
                'message_type': message_type,
                'is_automated': False,
                'send_status': 'sent',
                'created_at': now,
            })

            room.sudo().write({'last_message_time': now})

            # Post to Discuss (mail.thread) so message is visible in Odoo inbox
            try:
                room.sudo().message_post(
                    body=message_text,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                    author_id=request.env.user.partner_id.id,
                )
            except Exception:
                _logger.warning('Failed to post reply to Discuss for room %s', room_id, exc_info=True)

            msg_dict = self._message_to_dict(msg)
            self._notify_new_message(room_id, msg_dict)
            return request.make_json_response({
                'status': 'success',
                'data': msg_dict,
            })
        except Exception as e:
            _logger.error("reply_to_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-6: Close / archive chat
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/rooms/<int:room_id>/close', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def close_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/close — Archive/close chat.

        EPIC02 - PBI-22: Marks room state as 'done'.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            room.write({
                'state': 'done',
                'is_assigned': False,
                'assigned_to': False,
                'assigned_at': False,
            })

            return request.make_json_response({
                'status': 'success',
                'message': 'Chat berhasil ditutup.',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("close_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-8 (EPIC02): Schedule message
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/rooms/<int:room_id>/schedule', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def schedule_message(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/schedule — Schedule a message.

        EPIC05 - PBI-34: Saves scheduled message with send_at time.
        Body (JSON): { "message": "...", "send_at": "2026-03-01 09:00:00" }
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            message_text = (body.get('message') or '').strip()
            send_at_str = body.get('send_at', '')

            if not message_text:
                return request.make_json_response(
                    {'status': 'error', 'message': 'message tidak boleh kosong.'}, status=400
                )
            if not send_at_str:
                return request.make_json_response(
                    {'status': 'error', 'message': 'send_at wajib diisi.'}, status=400
                )

            try:
                send_at = fields.Datetime.from_string(send_at_str)
            except Exception:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Format send_at tidak valid. Gunakan: YYYY-MM-DD HH:MM:SS'}, status=400
                )

            scheduled = request.env['dke.scheduled.message'].sudo().create({
                'room_id': room_id,
                'message_content': message_text,
                'send_at': send_at,
                'state': 'pending',
                'created_by': request.env.user.id,
            })

            return request.make_json_response({
                'status': 'success',
                'message': 'Pesan terjadwal berhasil disimpan.',
                'data': {
                    'id': scheduled.id,
                    'room_id': room_id,
                    'message_content': scheduled.message_content,
                    'send_at': self._fmt_dt(scheduled.send_at),
                    'state': scheduled.state,
                },
            })
        except Exception as e:
            _logger.error("schedule_message error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-9: List available (unclaimed) chat rooms
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chats/available', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_available_chats(self, **kwargs):
        """GET /api/chats/available — List unassigned chat rooms.

        EPIC02 - PBI-9: Returns active rooms where is_assigned == False,
        sorted by last_message_time desc. Used by customer_care to pick
        up new incoming conversations.

        Syncs all discuss.channel (WhatsApp) conversations into
        dke.chat.room first, so Customer-Care users can see chats even
        though the native WhatsApp module is admin-only.

        Query Params:
          - page   : int (default 1)
          - limit  : int (default 20)
          - search : string (optional)
        """
        try:
            # Sync native WA discuss channels → dke.chat.room
            self._sync_whatsapp_channels()

            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)
            search = (kwargs.get('search') or '').strip()

            domain = [
                ('is_assigned', '=', False),
                ('state', '=', 'active'),
            ]
            if search:
                domain += [
                    '|',
                    ('customer_name', 'ilike', search),
                    ('external_conversation_id', 'ilike', search),
                ]

            Room = request.env['dke.chat.room'].sudo()
            total = Room.search_count(domain)
            rooms = Room.search(
                domain,
                limit=limit,
                offset=(page - 1) * limit,
                order='last_message_time desc',
            )

            data = []
            for r in rooms:
                d = self._room_to_dict(r, include_preview=True)
                # Enrich preview from discuss.channel if linked
                if r.discuss_channel_id:
                    msgs = request.env['mail.message'].sudo().search(
                        [('model', '=', 'discuss.channel'),
                         ('res_id', '=', r.discuss_channel_id.id),
                         ('message_type', 'in', ('comment', 'whatsapp_message'))],
                        order='create_date desc', limit=1,
                    )
                    if msgs:
                        body = re.sub(r'<[^>]+>', '', msgs.body or '').strip()
                        d['preview_message'] = body[:50]
                    d['message_count'] = request.env['mail.message'].sudo().search_count(
                        [('model', '=', 'discuss.channel'),
                         ('res_id', '=', r.discuss_channel_id.id),
                         ('message_type', 'in', ('comment', 'whatsapp_message'))],
                    )
                data.append(d)

            return request.make_json_response({
                'status': 'success',
                'meta': {
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': -(-total // limit) if total else 0,
                },
                'data': data,
            })
        except Exception as e:
            _logger.error("get_available_chats error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # PBI-9: Claim (take over) a chat room
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chats/<int:room_id>/claim', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def claim_chat(self, room_id, **kwargs):
        """POST /api/chats/{room_id}/claim — Claim an unassigned chat room.

        EPIC02 - PBI-9: Sets is_assigned=True, assigned_to=current user,
        assigned_at=now. Returns 409 if room is already claimed.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'},
                    status=404,
                )

            if room.is_assigned:
                return request.make_json_response(
                    {
                        'status': 'error',
                        'message': 'Chat sudah diambil oleh %s.' % (
                            room.assigned_to.name if room.assigned_to else 'agen lain'
                        ),
                    },
                    status=409,
                )

            room.write({
                'is_assigned': True,
                'assigned_to': request.env.user.id,
                'assigned_at': fields.Datetime.now(),
            })

            # If linked to a discuss.channel, add CC as member so they
            # can read/reply inside Odoo Discuss natively.
            if room.discuss_channel_id:
                try:
                    channel = room.discuss_channel_id.sudo()
                    partner = request.env.user.partner_id
                    already_member = channel.channel_member_ids.filtered(
                        lambda m: m.partner_id == partner
                    )
                    if not already_member:
                        channel.write({
                            'channel_member_ids': [Command.create({'partner_id': partner.id})],
                        })
                except Exception:
                    _logger.warning(
                        'Failed to add CC as discuss.channel member for room %s',
                        room_id, exc_info=True,
                    )

            # Post claim event to Discuss
            try:
                room.sudo().message_post(
                    body='Chat diambil oleh %s' % request.env.user.name,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
            except Exception:
                _logger.warning('Failed to post claim note to Discuss for room %s', room_id, exc_info=True)

            # Notify via bus so other CC agents see the update in real-time
            try:
                request.env['bus.bus']._sendone(
                    'dke_chat_available',
                    'chat.claimed',
                    {'room_id': room_id, 'claimed_by': request.env.user.name},
                )
            except Exception:
                pass

            return request.make_json_response({
                'status': 'success',
                'message': 'Chat berhasil diambil.',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("claim_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Extra: Single room detail
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/rooms/<int:room_id>', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_detail(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id} — Get single room detail."""
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )
            return request.make_json_response({
                'status': 'success',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("get_room_detail error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Helper: bus.bus notification for new messages
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _notify_new_message(room_id, msg_dict):
        """Send bus.bus notification so other clients pick up new messages."""
        try:
            request.env['bus.bus']._sendone(
                'dke_chat_room_%s' % room_id,
                'chat.new_message',
                {'room_id': room_id, 'message': msg_dict},
            )
        except Exception:
            _logger.debug('bus.bus notification failed for room %s', room_id, exc_info=True)

    # ──────────────────────────────────────────────────────────────
    # File upload for chat attachments
    # ──────────────────────────────────────────────────────────────

    ALLOWED_EXTENSIONS = {
        'image': {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'},
        'file':  {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt', 'zip'},
    }
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    @http.route('/api/chat/rooms/<int:room_id>/upload', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def upload_attachment(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/upload — Upload file/image and send as message.

        Multipart form data:
          - file    : uploaded file (required)
          - caption : optional text caption
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            # Only the assigned CC can upload
            current_uid = request.env.user.id
            assigned_uid = room.assigned_to.id if room.assigned_to else None
            if room.is_assigned and assigned_uid and assigned_uid != current_uid:
                _logger.info(
                    'Upload blocked: room %s assigned to uid=%s, request uid=%s',
                    room_id, assigned_uid, current_uid,
                )
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat ini ditangani oleh %s.' % room.assigned_to.name},
                    status=403,
                )

            uploaded = request.httprequest.files.get('file')
            if not uploaded or not uploaded.filename:
                return request.make_json_response(
                    {'status': 'error', 'message': 'File wajib diupload.'}, status=400
                )

            filename = uploaded.filename
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            # Determine message_type from extension
            if ext in self.ALLOWED_EXTENSIONS['image']:
                message_type = 'image'
            elif ext in self.ALLOWED_EXTENSIONS['file']:
                message_type = 'file'
            else:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Tipe file tidak diizinkan: .%s' % ext},
                    status=400,
                )

            file_data = uploaded.read()
            if len(file_data) > self.MAX_FILE_SIZE:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ukuran file melebihi 10 MB.'}, status=400
                )

            caption = (kwargs.get('caption') or '').strip()
            now = fields.Datetime.now()

            # Store as ir.attachment
            attachment = request.env['ir.attachment'].sudo().create({
                'name': filename,
                'datas': base64.b64encode(file_data),
                'res_model': 'dke.chat.room',
                'res_id': room_id,
                'type': 'binary',
                'mimetype': uploaded.content_type or 'application/octet-stream',
            })
            attachment_url = '/web/content/%d?download=true' % attachment.id

            # ── Linked to native WhatsApp discuss.channel ───────
            if room.discuss_channel_id:
                channel = room.discuss_channel_id.sudo()
                body_html = ''
                if caption:
                    body_html = '<p>%s</p>' % caption
                new_mail_msg = channel.message_post(
                    body=body_html,
                    message_type='whatsapp_message',
                    subtype_xmlid='mail.mt_comment',
                    author_id=request.env.user.partner_id.id,
                    attachment_ids=[attachment.id],
                )
                room.write({'last_message_time': now})

                msg_dict = self._discuss_msg_to_dict(new_mail_msg, channel)
                msg_dict['attachment_url'] = attachment_url
                msg_dict['attachment_filename'] = filename
                msg_dict['message_type'] = message_type
                if caption:
                    msg_dict['content_text'] = caption

                self._notify_new_message(room_id, msg_dict)
                return request.make_json_response({
                    'status': 'success',
                    'data': msg_dict,
                })

            # ── Fallback: legacy dke.chat.message ───────────────
            msg = request.env['dke.chat.message'].sudo().create({
                'room_id': room_id,
                'sender_type': 'admin',
                'sender_id': request.env.user.id,
                'content_text': caption or filename,
                'message_type': message_type,
                'attachment_url': attachment_url,
                'is_automated': False,
                'send_status': 'sent',
                'created_at': now,
            })

            room.sudo().write({'last_message_time': now})

            msg_dict = self._message_to_dict(msg)
            self._notify_new_message(room_id, msg_dict)
            return request.make_json_response({
                'status': 'success',
                'data': msg_dict,
            })
        except Exception as e:
            _logger.error("upload_attachment error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )
