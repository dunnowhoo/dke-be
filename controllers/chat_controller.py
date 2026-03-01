# -*- coding: utf-8 -*-

from odoo import http, fields
from odoo.http import request
from datetime import datetime
import json
import logging

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
        """Return ISO-8601 string or None."""
        return fields.Datetime.to_string(dt) if dt else None

    @staticmethod
    def _room_to_dict(room):
        return {
            'id': room.id,
            'name': room.name,
            'customer_name': room.customer_name or '',
            'customer_phone': room.external_conversation_id or '',
            'customer_id': room.customer_id.id if room.customer_id else None,
            'source': room.source,
            'state': room.state,
            'is_assigned': room.is_assigned,
            'assigned_to': room.assigned_to.name if room.assigned_to else None,
            'assigned_at': ChatController._fmt_dt(room.assigned_at),
            'unread_count': room.unread_count,
            'last_message_time': ChatController._fmt_dt(room.last_message_time),
        }

    @staticmethod
    def _message_to_dict(msg):
        return {
            'id': msg.id,
            'external_message_id': msg.external_message_id or '',
            'sender_type': msg.sender_type,
            'sender_name': msg.sender_id.name if msg.sender_id else None,
            'content': msg.content_text or '',
            'message_type': msg.message_type,
            'attachment_url': msg.attachment_url or '',
            'is_read': msg.is_read,
            'is_automated': msg.is_automated,
            'send_status': msg.send_status,
            'created_at': ChatController._fmt_dt(msg.created_at),
        }

    # ──────────────────────────────────────────────────────────────
    # PBI-2: List chat rooms
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/chat/list', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_chat_list(self, **kwargs):
        """GET /api/chat/list — List all chat rooms with pagination and filter.

        EPIC02 - PBI-9: Returns conversations sorted by last_message_time desc.
        Query Params:
          - page   : int (default 1)
          - limit  : int (default 20)
          - source : 'whatsapp' | 'shopee' | 'platform' (optional)
          - state  : 'active' | 'done' | 'archived' (optional)
          - search : string — cari customer_name / phone (optional)
        """
        try:
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
          - page  : int (default 1)
          - limit : int (default 50)
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 50)), 200)

            Msg = request.env['dke.chat.message'].sudo()
            domain = [('room_id', '=', room_id)]
            total = Msg.search_count(domain)
            messages = Msg.search(domain, limit=limit, offset=(page - 1) * limit)

            # Mark unread customer messages as read
            unread = messages.filtered(lambda m: not m.is_read and m.sender_type == 'customer')
            if unread:
                unread.write({'is_read': True})
                room.write({'unread_count': max(room.unread_count - len(unread), 0)})

            return request.make_json_response({
                'status': 'success',
                'room': self._room_to_dict(room),
                'meta': {
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': -(-total // limit),
                },
                'data': [self._message_to_dict(m) for m in messages],
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

        EPIC02 - PBI-21: Saves admin reply to dke.chat.message.
        Body (JSON): { "message": "...", "type": "text" }
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
            message_type = body.get('type', 'text')

            if not message_text:
                return request.make_json_response(
                    {'status': 'error', 'message': 'message tidak boleh kosong.'}, status=400
                )

            if message_type not in ('text', 'image', 'file'):
                message_type = 'text'

            now = fields.Datetime.now()
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

            return request.make_json_response({
                'status': 'success',
                'data': self._message_to_dict(msg),
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
        """POST /api/chat/rooms/{room_id}/close — Close/archive chat.

        EPIC02 - PBI-22: Marks room state as 'done'.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Chat room tidak ditemukan.'}, status=404
                )

            room.write({'state': 'done'})

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
