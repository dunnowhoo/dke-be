# -*- coding: utf-8 -*-

from odoo import http, fields
from odoo.http import request
from datetime import datetime
import json
import logging

_logger = logging.getLogger(__name__)


class TicketingController(http.Controller):
    """REST API endpoints for Ticketing System.

    EPIC02 - PBI-9  : Melihat Daftar Semua Chat dari WhatsApp
    EPIC02 - PBI-20 : Melihat Detail Riwayat Percakapan Pelanggan
    EPIC02 - PBI-21 : Mengirim Pesan Balasan ke Pelanggan
    EPIC02 - PBI-22 : Mengakhiri Sesi Chat dengan Pelanggan
    EPIC05 - PBI-34 : Menjadwalkan Pesan Follow-Up Manual
    """

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_dt(dt):
        if not dt:
            return None
        s = fields.Datetime.to_string(dt)
        return s + 'Z' if s and not s.endswith('Z') else s

    @staticmethod
    def _room_to_dict(room):
        """Unified room dict — mirrors TicketingRoom.to_dict() on the model."""
        active_session = room.get_active_session() if hasattr(room, 'get_active_session') else False
        assigned_name = ''
        assigned_id = None
        if room.assigned_to:
            assigned_name = room.assigned_to.name or ''
            assigned_id = room.assigned_to.id

        # Last message preview
        last_msg = room.message_ids[:1] if room.message_ids else False
        preview = ''
        preview_sender = ''
        if last_msg:
            preview = (last_msg.content_text or '')[:80]
            preview_sender = last_msg.sender_type or ''

        return {
            'id': room.id,
            'name': room.name,
            'customer_name': room.customer_name or '',
            'customer_phone': room.customer_phone or room.external_conversation_id or '',
            'customer_initial': room.customer_initial or '--',
            'platform': room.source or 'platform',
            'state': room.state,
            'assigned_cs': assigned_name,
            'assigned_cs_id': assigned_id,
            'last_message_time': TicketingController._fmt_dt(room.last_message_time),
            'unread_count': room.unread_count,
            'session_id': active_session.id if active_session else None,
            'session_code': active_session.session_code if active_session else None,
            'customer_rating': active_session.customer_rating if active_session else None,
            'last_message_preview': preview,
            'last_message_sender_type': preview_sender,
        }

    @staticmethod
    def _message_to_dict(msg):
        att_url = msg.attachment_url or ''
        if not att_url and msg.attachment_id:
            att_url = '/web/content/%d?download=true' % msg.attachment_id.id

        return {
            'id': msg.id,
            'room_id': msg.room_id.id,
            'session_id': msg.session_id.id if msg.session_id else None,
            'sender_type': msg.sender_type,
            'sender_id': msg.sender_id.id if msg.sender_id else None,
            'agent_name': msg.agent_name or (msg.sender_id.name if msg.sender_id else ''),
            'content_text': msg.content_text or '',
            'message_type': msg.message_type,
            'attachment_url': att_url,
            'attachment_name': msg.attachment_name or '',
            'attachment_size': msg.attachment_size or 0,
            'attachment_mimetype': msg.attachment_mimetype or '',
            'is_read': msg.is_read,
            'send_status': msg.send_status,
            'created_at': TicketingController._fmt_dt(msg.created_at),
        }

    @staticmethod
    def _ticket_to_dict(ticket):
        return {
            'id': ticket.id,
            'name': ticket.name,
            'subject': ticket.subject or '',
            'description': ticket.description or '',
            'customer_name': ticket.customer_id.name if ticket.customer_id else '',
            'room_id': ticket.room_id.id if ticket.room_id else None,
            'room_name': ticket.room_id.customer_name if ticket.room_id else '',
            'created_by': ticket.created_by_id.name if ticket.created_by_id else '',
            'created_by_id': ticket.created_by_id.id if ticket.created_by_id else None,
            'assigned_expert': ticket.assigned_expert_id.name if ticket.assigned_expert_id else '',
            'assigned_expert_id': ticket.assigned_expert_id.id if ticket.assigned_expert_id else None,
            'priority': ticket.priority,
            'state': ticket.state,
            'sla_deadline': TicketingController._fmt_dt(ticket.sla_deadline),
            'is_overdue': ticket.is_overdue,
            'first_response_at': TicketingController._fmt_dt(ticket.first_response_at),
            'resolved_at': TicketingController._fmt_dt(ticket.resolved_at),
            'created_at': TicketingController._fmt_dt(ticket.create_date),
            'last_reply_at': None,
            'message_count': len(ticket.ticket_message_ids) if ticket.ticket_message_ids else 0,
        }

    # ──────────────────────────────────────────────────────────────
    # User profile endpoint
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/user/me', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_current_user(self, **kwargs):
        """GET /api/user/me — Returns current logged-in user profile."""
        try:
            user = request.env.user
            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': user.id,
                    'name': user.name,
                    'email': user.email or user.login,
                    'phone': user.dke_phone or '',
                    'role': user.dke_role or '',
                    'status': user.dke_status or 'active',
                    'specialization': user.dke_specialization or '',
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % user.id,
                    'avg_response_time': user.avg_response_time or 0,
                    'avg_resolution_time': user.avg_resolution_time or 0.0,
                    'avg_rating': user.avg_rating or 0,
                    'total_chats_handled': user.total_chats_handled or 0,
                    'total_tickets_resolved': user.total_tickets_resolved or 0,
                    'total_messages_sent': user.total_messages_sent or 0,
                },
            })
        except Exception as e:
            _logger.error("get_current_user error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # List Ticketing Rooms
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/list', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_chat_list(self, **kwargs):
        """GET /api/chat/list — List Ticketing Rooms visible to the current user."""
        try:
            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)
            source = kwargs.get('source')
            state = kwargs.get('state')
            search = kwargs.get('search', '').strip()

            user = request.env.user
            uid_str = str(user.id)

            # Only show rooms where the current user is a participant
            # assigned_to = creator, external_conversation_id = partner user ID
            domain = [
                '|',
                ('assigned_to', '=', user.id),
                ('external_conversation_id', '=', uid_str),
            ]
            if source:
                domain.append(('source', '=', source))
            if state:
                domain.append(('state', '=', state))
            if search:
                domain += ['|',
                           ('customer_name', 'ilike', search),
                           ('name', 'ilike', search)]

            Room = request.env['dke.ticketing.room'].sudo()
            total = Room.search_count(domain)
            rooms = Room.search(domain, limit=limit, offset=(page - 1) * limit)

            return request.make_json_response({
                'status': 'success',
                'meta': {
                    'total': total,
                    'page': page,
                    'limit': limit,
                    'pages': -(-total // limit),
                },
                'data': [self._room_to_dict(r) for r in rooms],
            })
        except Exception as e:
            _logger.error("get_chat_list error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Message history
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/messages', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_messages(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}/messages"""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
                )

            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 50)), 200)

            Msg = request.env['dke.ticketing.message'].sudo()
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
    # Reply to chat
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/reply', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reply_to_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/reply"""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
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
            user = request.env.user
            msg = request.env['dke.ticketing.message'].sudo().create({
                'room_id': room_id,
                'sender_type': 'cs',
                'sender_id': user.id,
                'agent_name': user.name,
                'content_text': message_text,
                'message_type': message_type,
                'is_automated': False,
                'send_status': 'sent',
                'created_at': now,
            })

            room.sudo().write({'last_message_time': now})

            # Auto-assign if not already
            if not room.assigned_to:
                room.sudo().write({
                    'assigned_to': user.id,
                    'is_assigned': True,
                    'assigned_at': now,
                })
            
            # Increment total_messages_sent for the user
            user.sudo().write({'total_messages_sent': (user.total_messages_sent or 0) + 1})

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
    # Close / archive chat
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/close', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def close_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/close"""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
                )

            room.write({'state': 'done'})

            # Close active session if any
            active_session = room.get_active_session()
            if active_session:
                active_session.action_close()

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
    # Assign / Take Over chat
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/assign', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def assign_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/assign — Assign current user to chat."""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
                )

            user = request.env.user
            now = fields.Datetime.now()
            room.write({
                'assigned_to': user.id,
                'is_assigned': True,
                'assigned_at': now,
            })

            return request.make_json_response({
                'status': 'success',
                'message': 'Chat berhasil di-assign.',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("assign_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Create new Ticketing Room
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/create', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_ticketing_room(self, **kwargs):
        """POST /api/chat/rooms/create — Create a new Ticketing Room."""
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            customer_name = (body.get('customer_name') or '').strip()
            customer_phone = (body.get('customer_phone') or '').strip()
            source = body.get('source', 'whatsapp')
            message = (body.get('message') or '').strip()

            if not customer_name:
                return request.make_json_response(
                    {'status': 'error', 'message': 'customer_name wajib diisi.'}, status=400
                )

            if source not in ('whatsapp', 'shopee', 'platform'):
                source = 'whatsapp'

            now = fields.Datetime.now()
            user = request.env.user

            room = request.env['dke.ticketing.room'].sudo().create({
                'name': customer_name,
                'customer_name': customer_name,
                'customer_phone': customer_phone,
                'source': source,
                'state': 'active',
                'assigned_to': user.id,
                'is_assigned': True,
                'assigned_at': now,
                'last_message_time': now,
            })

            # Create session
            request.env['dke.ticketing.session'].sudo().create({
                'room_id': room.id,
                'cs_user_id': user.id,
                'state': 'active',
            })

            # Send initial message if provided
            if message:
                request.env['dke.ticketing.message'].sudo().create({
                    'room_id': room.id,
                    'sender_type': 'cs',
                    'sender_id': user.id,
                    'agent_name': user.name,
                    'content_text': message,
                    'message_type': 'text',
                    'send_status': 'sent',
                    'created_at': now,
                })
                # Increment total_messages_sent for the user
                user.sudo().write({'total_messages_sent': (user.total_messages_sent or 0) + 1})


            return request.make_json_response({
                'status': 'success',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("create_ticketing_room error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ─── Direct Chat (find-or-create between two users) ──────────
    @http.route('/api/ticketing/direct', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def direct_chat(self, **kwargs):
        """POST /api/chat/direct — Find or create a direct Ticketing Room between current user and a partner.

        Body: { "partner_id": <int> }
        Returns: the room dict (existing or newly created).
        """
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            partner_id = int(body.get('partner_id', 0))

            if not partner_id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'partner_id wajib diisi.'}, status=400
                )

            partner = request.env['res.users'].sudo().browse(partner_id)
            if not partner.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'User partner tidak ditemukan.'}, status=404
                )

            user = request.env.user
            Room = request.env['dke.ticketing.room'].sudo()

            # Look for existing direct room between these two users
            # external_conversation_id stores partner user ID as string
            existing = Room.search([
                ('source', '=', 'platform'),
                '|',
                '&', ('assigned_to', '=', user.id), ('external_conversation_id', '=', str(partner_id)),
                '&', ('assigned_to', '=', partner_id), ('external_conversation_id', '=', str(user.id)),
            ], limit=1)

            if existing:
                return request.make_json_response({
                    'status': 'success',
                    'data': self._room_to_dict(existing),
                })

            # Create new direct room
            now = fields.Datetime.now()
            room_name = 'Direct: %s ↔ %s' % (user.name, partner.name)

            room = Room.create({
                'name': room_name,
                'customer_name': partner.name,
                'customer_phone': partner.dke_phone or partner.email or '',
                'external_conversation_id': str(partner_id),   # partner user ID for lookup
                'source': 'platform',
                'state': 'active',
                'assigned_to': user.id,
                'is_assigned': True,
                'assigned_at': now,
                'last_message_time': now,
            })

            # Create session
            request.env['dke.ticketing.session'].sudo().create({
                'room_id': room.id,
                'cs_user_id': user.id,
                'state': 'active',
            })

            # System welcome message
            request.env['dke.ticketing.message'].sudo().create({
                'room_id': room.id,
                'sender_type': 'system',
                'content_text': 'Chat dimulai antara %s dan %s' % (user.name, partner.name),
                'message_type': 'text',
                'send_status': 'sent',
                'created_at': now,
            })

            # ── Auto-create support ticket if CS is talking to Expert ──
            if user.dke_role == 'customer_care' and partner.dke_role == 'expert_staff':
                body = json.loads(request.httprequest.data or b'{}') if request.httprequest.data else {}
                subject = (body.get('subject') or room_name).strip()
                category = (body.get('category') or partner.dke_specialization or 'face_wash')
                request.env['dke.support.ticket'].sudo().create({
                    'name': request.env['ir.sequence'].sudo().next_by_code('dke.support.ticket') or 'TIK/NEW',
                    'subject': subject,
                    'description': 'Tiket otomatis dari percakapan: %s' % room_name,
                    'room_id': room.id,
                    'created_by_id': user.id,
                    'assigned_expert_id': partner_id,
                    'priority': body.get('priority', 'medium'),
                    'state': 'open',
                    'category': category,
                })

            return request.make_json_response({
                'status': 'success',
                'data': self._room_to_dict(room),
            })
        except Exception as e:
            _logger.error("direct_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # AI Chat Suggestion (Tanya DKE)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/suggestion', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_ai_suggestion(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}/suggestion — Get AI suggestion based on last messages."""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
                )

            # Get last few customer messages for context
            messages = request.env['dke.ticketing.message'].sudo().search([
                ('room_id', '=', room_id),
                ('sender_type', '=', 'customer'),
            ], limit=5, order='created_at desc')

            context_text = ' '.join([m.content_text or '' for m in messages])

            # Simple keyword-based suggestion engine (to be replaced with AI API)
            suggestion = self._generate_suggestion(context_text, room)

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'suggestion': suggestion,
                    'source': 'tanya_dke',
                },
            })
        except Exception as e:
            _logger.error("get_ai_suggestion error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    def _generate_suggestion(self, context, room):
        """Simple keyword-based suggestion. Replace with actual AI call."""
        ctx = context.lower()
        if any(w in ctx for w in ['kirim', 'delivery', 'paket', 'sampai']):
            return 'Periksa status pengiriman di dashboard logistik dan berikan update ke pelanggan.'
        if any(w in ctx for w in ['harga', 'price', 'diskon', 'promo']):
            return 'Cek promo terbaru dan tawarkan voucher member untuk mendorong transaksi.'
        if any(w in ctx for w in ['rusak', 'cacat', 'complain', 'keluhan']):
            return 'Sampaikan permohonan maaf, tawarkan refund/replacement, dan buat tiket eskalasi ke Expert Staff.'
        if any(w in ctx for w in ['bahan', 'kandungan', 'ingredient', 'aman']):
            return 'Informasikan kandungan produk atau tawarkan untuk menghubungkan dengan tim farmasi.'
        if any(w in ctx for w in ['stok', 'stock', 'ready', 'tersedia']):
            return 'Cek ketersediaan stok di sistem inventory dan informasikan ke pelanggan.'
        return 'Tawarkan bantuan tambahan atau produk serupa sesuai kebutuhan pelanggan.'

    # ──────────────────────────────────────────────────────────────
    # Ticket endpoints (for CS and Expert Staff dashboards)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/tickets', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_tickets(self, **kwargs):
        """GET /api/tickets — List tickets with role-based filtering.
        
        For expert_staff: shows tickets assigned to them.
        For customer_care: shows tickets they created.
        """
        try:
            user = request.env.user
            role = user.dke_role or ''
            state = kwargs.get('state')
            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 20)), 100)

            domain = []
            if role == 'expert_staff':
                domain.append(('assigned_expert_id', '=', user.id))
            elif role == 'customer_care':
                domain.append(('created_by_id', '=', user.id))
            # else: show all (for admins/managers)

            if state:
                domain.append(('state', '=', state))

            Ticket = request.env['dke.support.ticket'].sudo()
            total = Ticket.search_count(domain)
            tickets = Ticket.search(domain, limit=limit, offset=(page - 1) * limit)

            # Enrich with last reply time
            result = []
            for t in tickets:
                td = self._ticket_to_dict(t)
                last_msg = t.ticket_message_ids[-1:] if t.ticket_message_ids else False
                td['last_reply_at'] = self._fmt_dt(last_msg.created_at) if last_msg else None
                result.append(td)

            return request.make_json_response({
                'status': 'success',
                'meta': {'total': total, 'page': page, 'limit': limit, 'pages': -(-total // limit)},
                'data': result,
            })
        except Exception as e:
            _logger.error("get_tickets error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/create', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_ticket(self, **kwargs):
        """POST /api/tickets/create — Create escalation ticket (CS → Expert)."""
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}

            subject = (body.get('subject') or '').strip()
            description = (body.get('description') or '').strip()
            room_id = body.get('room_id')
            expert_id = body.get('assigned_expert_id')
            priority = body.get('priority', 'medium')

            if not subject:
                return request.make_json_response(
                    {'status': 'error', 'message': 'subject wajib diisi.'}, status=400
                )

            user = request.env.user
            vals = {
                'name': request.env['ir.sequence'].sudo().next_by_code('dke.support.ticket') or 'TKT/NEW',
                'subject': subject,
                'description': description,
                'created_by_id': user.id,
                'priority': priority if priority in ('low', 'medium', 'high', 'urgent') else 'medium',
                'state': 'open',
            }

            if room_id:
                room = request.env['dke.ticketing.room'].sudo().browse(int(room_id))
                if room.exists():
                    vals['room_id'] = room.id
                    vals['customer_id'] = room.customer_id.id if room.customer_id else None

            if expert_id:
                vals['assigned_expert_id'] = int(expert_id)
                vals['state'] = 'in_progress'

            ticket = request.env['dke.support.ticket'].sudo().create(vals)

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("create_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/<int:ticket_id>/reassign', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reassign_ticket(self, ticket_id, **kwargs):
        """POST /api/tickets/{ticket_id}/reassign — Reassign ticket to different expert.
        
        Creates a notification for the new expert staff.
        Body: { "new_expert_id": 5, "reason": "Expert tidak merespon" }
        """
        try:
            ticket = request.env['dke.support.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            new_expert_id = body.get('new_expert_id')
            reason = (body.get('reason') or '').strip()

            if not new_expert_id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'new_expert_id wajib diisi.'}, status=400
                )

            old_expert = ticket.assigned_expert_id.name if ticket.assigned_expert_id else 'Tidak ada'
            user = request.env.user

            ticket.write({
                'assigned_expert_id': int(new_expert_id),
                'state': 'in_progress',
            })

            # Add reassignment message as audit trail
            request.env['dke.support.ticket.message'].sudo().create({
                'ticket_id': ticket_id,
                'sender_id': user.id,
                'content': 'Tiket di-reassign dari %s ke %s. Alasan: %s' % (
                    old_expert, ticket.assigned_expert_id.name, reason or '-'
                ),
            })

            # Create notification for the new expert
            new_expert = request.env['res.users'].sudo().browse(int(new_expert_id))
            if new_expert.exists():
                request.env['dke.notification'].sudo().create({
                    'user_id': int(new_expert_id),
                    'title': 'Tiket Baru Ditugaskan',
                    'message': 'Anda ditugaskan untuk menangani tiket %s: %s' % (
                        ticket.name, ticket.subject
                    ),
                    'notification_type': 'ticket_assigned',
                    'reference_model': 'dke.support.ticket',
                    'reference_id': ticket.id,
                })

            return request.make_json_response({
                'status': 'success',
                'message': 'Tiket berhasil di-reassign.',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("reassign_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/<int:ticket_id>/reply', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reply_to_ticket(self, ticket_id, **kwargs):
        """POST /api/tickets/{ticket_id}/reply — Add internal message to ticket."""
        try:
            ticket = request.env['dke.support.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            content = (body.get('content') or '').strip()

            if not content:
                return request.make_json_response(
                    {'status': 'error', 'message': 'content wajib diisi.'}, status=400
                )
            user = request.env.user

            msg = request.env['dke.support.ticket.message'].sudo().create({
                'ticket_id': ticket_id,
                'sender_id': user.id,
                'content': content,
            })

            # Record first response time
            if not ticket.first_response_at:
                ticket.write({'first_response_at': fields.Datetime.now()})

            if user.dke_role == 'expert_staff':
                user.sudo().write({'total_messages_sent': (user.total_messages_sent or 0) + 1})

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': msg.id,
                    'sender': user.name,
                    'sender_id': user.id,
                    'content': msg.content,
                    'created_at': self._fmt_dt(msg.created_at),
                },
            })
        except Exception as e:
            _logger.error("reply_to_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/<int:ticket_id>/resolve', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def resolve_ticket(self, ticket_id, **kwargs):
        """POST /api/tickets/{ticket_id}/resolve — Mark ticket as resolved."""
        try:
            ticket = request.env['dke.support.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            ticket.write({
                'state': 'resolved',
                'resolved_at': fields.Datetime.now(),
            })

            # --- Calculate Resolution Velocity ---
            expert = ticket.assigned_expert_id
            if expert:
                diff = fields.Datetime.now() - ticket.create_date
                hours_taken = diff.total_seconds() / 3600.0
                current_total = expert.total_tickets_resolved or 0
                current_avg = expert.avg_resolution_time or 0.0
                new_total = current_total + 1
                new_avg = ((current_avg * current_total) + hours_taken) / new_total
                expert.sudo().write({
                    'total_tickets_resolved': new_total,
                    'avg_resolution_time': new_avg,
                })

            return request.make_json_response({
                'status': 'success',
                'message': 'Tiket berhasil di-resolve.',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("resolve_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/<int:ticket_id>/update', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def update_ticket(self, ticket_id, **post):
        """POST /api/tickets/{ticket_id}/update — Update ticket subject, desc, or priority."""
        try:
            if not request.httprequest.data:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Missing JSON payload.'}, status=400
                )
            
            payload = json.loads(request.httprequest.data.decode('utf-8'))
            ticket = request.env['dke.support.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            # Only customer_care or admin/manager allowed
            user = request.env.user
            if user.dke_role not in ('customer_care', 'sales_manager', 'admin'):
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya Customer Care yang dapat mengubah tiket.'}, status=403
                )

            vals = {}
            if 'subject' in payload:
                vals['subject'] = payload['subject']
            if 'description' in payload:
                vals['description'] = payload['description']
            if 'priority' in payload:
                vals['priority'] = payload['priority']

            ticket.write(vals)

            return request.make_json_response({
                'status': 'success',
                'message': 'Tiket berhasil diupdate.',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("update_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/tickets/<int:ticket_id>/delete', type='http', auth='user', methods=['DELETE', 'POST'], csrf=False, cors='*')
    def delete_ticket(self, ticket_id, **kwargs):
        """DELETE /api/tickets/{ticket_id}/delete — Delete a ticket."""
        try:
            ticket = request.env['dke.support.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            user = request.env.user
            if user.dke_role not in ('customer_care', 'sales_manager', 'admin'):
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya Customer Care yang dapat menghapus tiket.'}, status=403
                )

            ticket.unlink()
            return request.make_json_response({
                'status': 'success',
                'message': 'Tiket berhasil dihapus.',
            })
        except Exception as e:
            _logger.error("delete_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Notifications
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/notifications', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_notifications(self, **kwargs):
        """GET /api/notifications — Get notifications for current user."""
        try:
            user = request.env.user
            unread_only = kwargs.get('unread_only', 'false') == 'true'

            domain = [('user_id', '=', user.id)]
            if unread_only:
                domain.append(('is_read', '=', False))

            notifications = request.env['dke.notification'].sudo().search(
                domain, limit=50, order='create_date desc'
            )

            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': n.id,
                    'title': n.title,
                    'message': n.message,
                    'type': n.notification_type,
                    'is_read': n.is_read,
                    'reference_model': n.reference_model or '',
                    'reference_id': n.reference_id or 0,
                    'created_at': self._fmt_dt(n.create_date),
                } for n in notifications],
                'unread_count': request.env['dke.notification'].sudo().search_count([
                    ('user_id', '=', user.id),
                    ('is_read', '=', False),
                ]),
            })
        except Exception as e:
            _logger.error("get_notifications error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/notifications/<int:notif_id>/read', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def mark_notification_read(self, notif_id, **kwargs):
        """POST /api/notifications/{notif_id}/read — Mark notification as read."""
        try:
            notif = request.env['dke.notification'].sudo().browse(notif_id)
            if notif.exists() and notif.user_id.id == request.env.user.id:
                notif.write({'is_read': True})
            return request.make_json_response({'status': 'success'})
        except Exception as e:
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Expert staff list (for CS to select when creating tickets)
    # ──────────────────────────────────────────────────────────────

    SPECIALIZATION_LABELS = {
        'face_wash': 'Face Wash',
        'serum': 'Serum',
        'lotion': 'Lotion',
        'toner': 'Toner',
    }

    @http.route('/api/users/experts', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_expert_staff(self, **kwargs):
        """GET /api/users/experts — List available expert staff with expertise labels."""
        try:
            experts = request.env['res.users'].sudo().search([
                ('dke_role', '=', 'expert_staff'),
                ('dke_status', '=', 'active'),
            ])

            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': e.id,
                    'name': e.name,
                    'email': e.email or e.login,
                    'specialization': e.dke_specialization or '',
                    'specialization_label': self.SPECIALIZATION_LABELS.get(e.dke_specialization or '', ''),
                    'avg_rating': e.avg_rating or 0,
                    'avg_resolution_time': e.avg_resolution_time or 0.0,
                    'total_tickets_resolved': e.total_tickets_resolved or 0,
                    'total_messages_sent': e.total_messages_sent or 0,
                } for e in experts],
            })
        except Exception as e:
            _logger.error("get_expert_staff error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Customer Care staff list (so Expert Staff can find CS partners)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/users/care-staff', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_care_staff(self, **kwargs):
        """GET /api/users/care-staff — List available customer care staff."""
        try:
            care_users = request.env['res.users'].sudo().search([
                ('dke_role', '=', 'customer_care'),
                ('dke_status', '=', 'active'),
            ])

            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': u.id,
                    'name': u.name,
                    'email': u.email or u.login,
                } for u in care_users],
            })
        except Exception as e:
            _logger.error("get_care_staff error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Ticket stats (for CS dashboard)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/tickets/stats', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_ticket_stats(self, **kwargs):
        """GET /api/tickets/stats — Get ticket statistics for current user."""
        try:
            user = request.env.user
            role = user.dke_role or ''

            Ticket = request.env['dke.support.ticket'].sudo()

            if role == 'expert_staff':
                base_domain = [('assigned_expert_id', '=', user.id)]
            elif role == 'customer_care':
                base_domain = [('created_by_id', '=', user.id)]
            else:
                base_domain = []

            total = Ticket.search_count(base_domain)
            open_count = Ticket.search_count(base_domain + [('state', '=', 'open')])
            in_progress = Ticket.search_count(base_domain + [('state', '=', 'in_progress')])
            resolved = Ticket.search_count(base_domain + [('state', '=', 'resolved')])
            closed = Ticket.search_count(base_domain + [('state', '=', 'closed')])
            overdue = Ticket.search_count(base_domain + [('is_overdue', '=', True)])

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'total': total,
                    'open': open_count,
                    'in_progress': in_progress,
                    'resolved': resolved,
                    'closed': closed,
                    'overdue': overdue,
                },
            })
        except Exception as e:
            _logger.error("get_ticket_stats error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Schedule message
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/schedule', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def schedule_message(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/schedule"""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
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
                    {'status': 'error', 'message': 'Format send_at tidak valid.'}, status=400
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
    # Room detail
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_detail(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}"""
        try:
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
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
    # File / Media Upload
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/rooms/<int:room_id>/upload', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def upload_media(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/upload — Upload media (image/video/document).

        Stores the file as an ir.attachment record for ACID-safe binary storage.
        Multipart form: file=<binary>, caption=<text>, message_type=<image|video|document>
        """
        try:
            import base64
            room = request.env['dke.ticketing.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticketing Room tidak ditemukan.'}, status=404
                )

            uploaded_file = request.httprequest.files.get('file')
            if not uploaded_file:
                return request.make_json_response(
                    {'status': 'error', 'message': 'File wajib dikirim.'}, status=400
                )

            caption = request.httprequest.form.get('caption', '').strip()
            message_type = request.httprequest.form.get('message_type', '').strip()

            # Determine message_type from mimetype if not specified
            mimetype = uploaded_file.mimetype or 'application/octet-stream'
            if not message_type:
                if mimetype.startswith('image/'):
                    message_type = 'image'
                elif mimetype.startswith('video/'):
                    message_type = 'video'
                else:
                    message_type = 'document'

            if message_type not in ('image', 'video', 'document'):
                message_type = 'document'

            file_data = uploaded_file.read()
            file_name = uploaded_file.filename or 'file'
            file_size = len(file_data)

            # Create ir.attachment — stored in PostgreSQL (ACID-compliant)
            user = request.env.user
            attachment = request.env['ir.attachment'].sudo().create({
                'name': file_name,
                'datas': base64.b64encode(file_data).decode('utf-8'),
                'res_model': 'dke.ticketing.message',
                'res_id': 0,  # will be updated after message creation
                'mimetype': mimetype,
                'type': 'binary',
            })

            now = fields.Datetime.now()
            msg = request.env['dke.ticketing.message'].sudo().create({
                'room_id': room_id,
                'sender_type': 'cs',
                'sender_id': user.id,
                'agent_name': user.name,
                'content_text': caption or file_name,
                'message_type': message_type,
                'attachment_id': attachment.id,
                'attachment_name': file_name,
                'attachment_size': file_size,
                'attachment_mimetype': mimetype,
                'send_status': 'sent',
                'created_at': now,
            })

            # Update attachment res_id
            attachment.sudo().write({'res_id': msg.id})

            room.sudo().write({'last_message_time': now})

            # Auto-assign if not already
            if not room.assigned_to:
                room.sudo().write({
                    'assigned_to': user.id,
                    'is_assigned': True,
                    'assigned_at': now,
                })

            return request.make_json_response({
                'status': 'success',
                'data': self._message_to_dict(msg),
            })
        except Exception as e:
            _logger.error("upload_media error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )
