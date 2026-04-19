# -*- coding: utf-8 -*-

from odoo import http, fields
from odoo.http import request
from datetime import datetime, timedelta
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
        return fields.Datetime.to_string(dt) if dt else None

    SPECIALIZATION_LABELS = {
        'face_wash': 'Face Wash',
        'serum': 'Serum',
        'lotion': 'Lotion',
        'toner': 'Toner',
    }

    @staticmethod
    def _room_to_dict(room):
        """Unified room dict — includes linked ticket info so the chat bubble title = ticket subject."""
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

        # Primary ticket (helpdesk.ticket linked via channel_id)
        ticket = room.ticket_ids[:1] if room.ticket_ids else False

        return {
            'id': room.id,
            # Display name = ticket subject (chat bubble title)
            'name': ticket.name if ticket else room.name,
            'room_name': room.name,
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
            # Ticket link (helpdesk.ticket)
            'ticket_id': ticket.id if ticket else None,
            'ticket_number': ticket.ticket_number if ticket else '',
            'ticket_subject': ticket.name if ticket else '',
            'ticket_priority': ticket.priority if ticket else '',
            'ticket_state': ticket.stage_id.name if ticket and ticket.stage_id else '',
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
            'sender_role': msg.sender_id.dke_role if msg.sender_id and hasattr(msg.sender_id, 'dke_role') else '',
            'content_text': msg.content_text or '',
            'message_type': msg.message_type,
            'attachment_url': att_url,
            'attachment_name': msg.attachment_name or '',
            'attachment_size': msg.attachment_size or 0,
            'attachment_mimetype': msg.attachment_mimetype or '',
            'is_read': msg.is_read,
            'read_at': TicketingController._fmt_dt(msg.read_at) if msg.read_at else None,
            'delivered': msg.delivered,
            'delivered_at': TicketingController._fmt_dt(msg.delivered_at) if msg.delivered_at else None,
            'send_status': msg.send_status,
            'created_at': TicketingController._fmt_dt(msg.created_at),
        }

    @staticmethod
    def _notify_bus(env, channel, event_type, payload):
        """Send bus.bus notification (best-effort, never breaks caller)."""
        try:
            env['bus.bus']._sendone(channel, event_type, payload)
        except Exception as e:
            _logger.warning("bus notification failed: %s", e)

    @staticmethod
    def _ticket_to_dict(ticket):
        """Serialize helpdesk.ticket to dict for API response."""
        stage = ticket.stage_id
        # SLA status derivation
        if ticket.sla_fail:
            sla_status = 'failed'
        elif ticket.sla_reached:
            sla_status = 'reached'
        else:
            sla_status = 'ongoing'

        return {
            'id': ticket.id,
            'name': ticket.ticket_ref or '',
            'ticket_number': ticket.ticket_number or '',
            'subject': ticket.name or '',
            'description': ticket.description or '',
            'resolution_note': ticket.resolution_notes or '',
            'resolution_category': ticket.resolution_category or '',
            'priority': ticket.priority or '0',
            'kanban_state': ticket.kanban_state or 'normal',
            'stage_id': [stage.id, stage.name] if stage else False,
            'team_id': [ticket.team_id.id, ticket.team_id.name] if ticket.team_id else False,
            'user_id': [ticket.user_id.id, ticket.user_id.name] if ticket.user_id else False,
            'partner_id': [ticket.partner_id.id, ticket.partner_id.name] if ticket.partner_id else False,
            'category_id': [ticket.ticket_type_id.id, ticket.ticket_type_id.name] if ticket.ticket_type_id else False,
            'tag_ids': ticket.tag_ids.ids,
            'tag_names': [t.name for t in ticket.tag_ids],
            'sla_status': sla_status,
            'sla_deadline': TicketingController._fmt_dt(ticket.sla_deadline),
            'days_since_opening': ticket.open_hours // 24 if ticket.open_hours else 0,
            'is_overdue': ticket.sla_fail or False,
            'is_close': stage.fold if stage else False,
            'customer_name': ticket.partner_name or (ticket.partner_id.name if ticket.partner_id else ''),
            'customer_email': ticket.partner_email or '',
            'assigned_user_name': ticket.user_id.name if ticket.user_id else 'Belum ditugaskan',
            'team_name': ticket.team_id.name if ticket.team_id else '',
            'stage_name': stage.name if stage else '',
            'channel_id': ticket.channel_id.id if ticket.channel_id else None,
            'create_date': TicketingController._fmt_dt(ticket.create_date),
            'write_date': TicketingController._fmt_dt(ticket.write_date),
            'date_closed': TicketingController._fmt_dt(ticket.close_date),
            'color': ticket.color or 0,
            # SLA timestamps
            'escalated_at': TicketingController._fmt_dt(ticket.escalated_at),
            'expert_assigned_at': TicketingController._fmt_dt(ticket.expert_assigned_at),
            'expert_first_response_at': TicketingController._fmt_dt(ticket.expert_first_response_at),
            'expert_resolved_at': TicketingController._fmt_dt(ticket.expert_resolved_at),
            'last_message_at': TicketingController._fmt_dt(ticket.last_message_at),
            # Assignment history
            'assignment_history': [{
                'id': h.id,
                'assigned_from': h.assigned_from_id.name if h.assigned_from_id else None,
                'assigned_from_id': h.assigned_from_id.id if h.assigned_from_id else None,
                'assigned_to': h.assigned_to_id.name if h.assigned_to_id else None,
                'assigned_to_id': h.assigned_to_id.id if h.assigned_to_id else None,
                'assigned_by': h.assigned_by_id.name if h.assigned_by_id else None,
                'assigned_by_id': h.assigned_by_id.id if h.assigned_by_id else None,
                'reason': h.reason or '',
                'assigned_at': TicketingController._fmt_dt(h.assigned_at),
            } for h in ticket.assignment_history_ids],
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

            # Mark unread messages from other senders as read (not just customer-type)
            current_uid = request.env.user.id
            unread = messages.filtered(
                lambda m: not m.is_read and (not m.sender_id or m.sender_id.id != current_uid)
            )
            if unread:
                now = fields.Datetime.now()
                unread.write({'is_read': True, 'read_at': now})
                room.write({'unread_count': max(room.unread_count - len(unread), 0)})
                # Notify sender that messages were read
                self._notify_bus(
                    request.env,
                    'dke_ticket_room_%s' % room_id,
                    'ticketing.messages_read',
                    {'room_id': room_id, 'message_ids': unread.ids, 'read_at': now.isoformat()},
                )

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
    # Mark messages as delivered (recipient received via bus / loaded chat)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/messages/mark-delivered', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def mark_messages_delivered(self, **kwargs):
        """POST /api/ticketing/messages/mark-delivered — Mark messages as delivered to recipient."""
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            message_ids = body.get('message_ids') or []
            if not isinstance(message_ids, list) or not message_ids:
                return request.make_json_response(
                    {'status': 'error', 'message': 'message_ids wajib diisi.'}, status=400
                )

            Msg = request.env['dke.ticketing.message'].sudo()
            user_id = request.env.user.id
            messages = Msg.browse([int(mid) for mid in message_ids]).exists()
            # Only mark as delivered messages NOT sent by the current user and not yet delivered
            targets = messages.filtered(
                lambda m: not m.delivered and (not m.sender_id or m.sender_id.id != user_id)
            )
            if not targets:
                return request.make_json_response({
                    'status': 'success',
                    'data': {'delivered_ids': []},
                })

            now = fields.Datetime.now()
            targets.write({'delivered': True, 'delivered_at': now})

            # Group by room to emit one bus event per room
            room_to_ids = {}
            for m in targets:
                room_to_ids.setdefault(m.room_id.id, []).append(m.id)
            for room_id, ids in room_to_ids.items():
                self._notify_bus(
                    request.env,
                    'dke_ticket_room_%s' % room_id,
                    'ticketing.messages_delivered',
                    {'room_id': room_id, 'message_ids': ids, 'delivered_at': now.isoformat()},
                )

            return request.make_json_response({
                'status': 'success',
                'data': {'delivered_ids': targets.ids},
            })
        except Exception as e:
            _logger.error("mark_messages_delivered error: %s", e, exc_info=True)
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
                'delivered': True,
                'delivered_at': now,
                'created_at': now,
            })

            room.sudo().write({'last_message_time': now})

            # Update ticket timestamps
            ticket = request.env['helpdesk.ticket'].sudo().search([('channel_id', '=', room_id)], limit=1)
            if ticket:
                ticket_vals = {'last_message_at': now}
                if user.dke_role == 'expert_staff' and not ticket.expert_first_response_at:
                    ticket_vals['expert_first_response_at'] = now
                ticket.write(ticket_vals)

            # Auto-assign if not already
            if not room.assigned_to:
                room.sudo().write({
                    'assigned_to': user.id,
                    'is_assigned': True,
                    'assigned_at': now,
                })

            # Increment total_messages_sent for the user
            user.sudo().write({'total_messages_sent': (user.total_messages_sent or 0) + 1})

            # Real-time notification via bus.bus
            self._notify_bus(
                request.env,
                'dke_ticket_room_%s' % room_id,
                'ticketing.new_message',
                {'room_id': room_id, 'message': self._message_to_dict(msg)},
            )

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

            # Bus notification
            self._notify_bus(
                request.env,
                'dke_ticket_room_%s' % room_id,
                'ticketing.room_closed',
                {'room_id': room_id},
            )

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

            # Bus notification
            self._notify_bus(
                request.env,
                'dke_ticket_room_%s' % room_id,
                'ticketing.room_assigned',
                {'room_id': room_id, 'assigned_to': user.name, 'assigned_to_id': user.id},
            )

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
        """POST /api/ticketing/rooms/create — Create a new Ticketing Room + auto-create linked Support Ticket.

        Body:
          title             (required) — Chat bubble title / ticket subject
          customer_name     (required) — Customer display name
          customer_phone    optional
          source            whatsapp|shopee|platform  (default: whatsapp)
          priority          low|medium|high|urgent    (default: medium)
          topic             product_inquiry|order_complaint|… (default: other)
          required_specialization  face_wash|serum|lotion|toner  (optional)
          description       optional ticket description
          message           optional first chat message to send
        """
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            title = (body.get('title') or '').strip()
            customer_name = (body.get('customer_name') or '').strip()
            customer_phone = (body.get('customer_phone') or '').strip()
            source = body.get('source', 'whatsapp')
            priority = body.get('priority', 'medium')
            topic = body.get('topic', 'other')
            required_specialization = body.get('required_specialization') or False
            description = (body.get('description') or '').strip()
            message = (body.get('message') or '').strip()

            if not title:
                return request.make_json_response(
                    {'status': 'error', 'message': 'title (judul tiket/chat) wajib diisi.'}, status=400
                )
            if not customer_name:
                return request.make_json_response(
                    {'status': 'error', 'message': 'customer_name wajib diisi.'}, status=400
                )

            if source not in ('whatsapp', 'shopee', 'platform'):
                source = 'whatsapp'
            if priority not in ('low', 'medium', 'high', 'urgent'):
                priority = 'medium'
            valid_topics = ('product_inquiry', 'order_complaint', 'return_refund', 'shipment', 'payment', 'other')
            if topic not in valid_topics:
                topic = 'other'
            valid_specs = ('face_wash', 'serum', 'lotion', 'toner')
            if required_specialization not in valid_specs:
                required_specialization = False

            now = fields.Datetime.now()
            user = request.env.user

            # Room name = title (the chat bubble display name)
            # Model create() auto-creates linked helpdesk.ticket via anti-recursion guard
            room = request.env['dke.ticketing.room'].sudo().create({
                'name': title,
                'customer_name': customer_name,
                'customer_phone': customer_phone,
                'source': source,
                'state': 'active',
                'assigned_to': user.id,
                'is_assigned': True,
                'assigned_at': now,
                'last_message_time': now,
            })

            # Update auto-created ticket with extra fields from payload
            ticket = room.ticket_ids[:1]
            if ticket:
                extra_vals = {}
                if description:
                    extra_vals['description'] = description
                mapped_priority = priority if priority in ('0', '1', '2', '3') else '0'
                if mapped_priority != '0':
                    extra_vals['priority'] = mapped_priority
                if extra_vals:
                    ticket.sudo().write(extra_vals)

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
                    'delivered': True,
                    'delivered_at': now,
                    'created_at': now,
                })
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
        """POST /api/chat/direct — Create a new direct Ticketing Room between current user and a partner.

        Each call always creates a NEW room (keyed by ticket subject, not by contact pair).
        Body: { "partner_id": <int>, "subject": <str>, "category": <str> }
        Returns: the newly created room dict.
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

            subject = (body.get('subject') or '').strip()
            category = (body.get('category') or partner.dke_specialization or 'face_wash')

            if not subject:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Judul tiket (subject) wajib diisi.'}, status=400
                )

            # Always create a new room — primary key is ticket subject, not contact pair
            now = fields.Datetime.now()
            room_name = subject  # room name = ticket title

            room = Room.create({
                'name': room_name,
                'customer_name': partner.name,
                'customer_phone': partner.dke_phone or partner.email or '',
                'external_conversation_id': str(partner_id),
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

            # Model create() auto-created helpdesk.ticket — update with extra fields
            ticket = room.ticket_ids[:1]
            if ticket:
                extra_vals = {
                    'description': 'Tiket otomatis dari percakapan: %s' % room_name,
                }
                if body.get('priority', '0') != '0':
                    extra_vals['priority'] = body.get('priority', '0')
                if partner.dke_role == 'expert_staff':
                    extra_vals['user_id'] = partner_id
                ticket.sudo().write(extra_vals)
                _logger.info("Auto-created helpdesk ticket %s for room %s", ticket.ticket_ref, room.id)

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
    # Helpdesk Ticket endpoints (using Enterprise helpdesk.ticket)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/tickets', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_tickets(self, **kwargs):
        """GET /api/ticketing/tickets — List helpdesk tickets.

        By default shows ALL tickets (shared kanban view).
        Pass my_tickets=1 to filter to current user's tickets only.
        """
        try:
            user = request.env.user
            role = user.dke_role or ''
            Ticket = request.env['helpdesk.ticket'].sudo()

            domain = []

            # my_tickets filter: only show user's own tickets
            my_tickets = kwargs.get('my_tickets', '').strip()
            if my_tickets == '1':
                if role == 'expert_staff':
                    domain = [('user_id', '=', user.id)]
                elif role == 'customer_care':
                    domain = [('create_uid', '=', user.id)]

            stage_id = kwargs.get('stage_id')
            team_id = kwargs.get('team_id')
            priority = kwargs.get('priority')
            user_id_filter = kwargs.get('user_id')
            tag_id = kwargs.get('tag_id')
            search = (kwargs.get('search') or '').strip()
            page = max(int(kwargs.get('page', 1)), 1)
            limit = min(int(kwargs.get('limit', 50)), 200)

            if stage_id:
                domain.append(('stage_id', '=', int(stage_id)))
            if team_id:
                domain.append(('team_id', '=', int(team_id)))
            if priority:
                domain.append(('priority', '=', priority))
            if user_id_filter:
                domain.append(('user_id', '=', int(user_id_filter)))
            if tag_id:
                domain.append(('tag_ids', 'in', [int(tag_id)]))
            if search:
                domain += ['|', ('name', 'ilike', search), ('ticket_ref', 'ilike', search)]

            total = Ticket.search_count(domain)
            tickets = Ticket.search(domain, limit=limit, offset=(page - 1) * limit, order='create_date desc')

            return request.make_json_response({
                'status': 'success',
                'data': [self._ticket_to_dict(t) for t in tickets],
                'meta': {'total': total, 'page': page, 'limit': limit, 'pages': -(-total // limit)},
            })
        except Exception as e:
            _logger.error("get_helpdesk_tickets error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_helpdesk_ticket(self, **kwargs):
        """POST /api/ticketing/tickets — Create a new helpdesk ticket."""
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}

            subject = (body.get('subject') or '').strip()
            if not subject:
                return request.make_json_response(
                    {'status': 'error', 'message': 'subject wajib diisi.'}, status=400
                )

            vals = {
                'name': subject,
                'description': body.get('description', ''),
                'priority': body.get('priority', '0'),
            }

            # Auto-pick default team if not provided
            if body.get('team_id'):
                vals['team_id'] = int(body['team_id'])
            else:
                default_team = request.env['helpdesk.team'].sudo().search([], limit=1)
                if default_team:
                    vals['team_id'] = default_team.id

            # Accept expert_staff_id as alias for user_id
            if body.get('expert_staff_id'):
                vals['user_id'] = int(body['expert_staff_id'])
            elif body.get('user_id'):
                vals['user_id'] = int(body['user_id'])

            if body.get('partner_id'):
                vals['partner_id'] = int(body['partner_id'])
            if body.get('category_id'):
                vals['ticket_type_id'] = int(body['category_id'])
            if body.get('tag_ids'):
                vals['tag_ids'] = [(6, 0, body['tag_ids'])]
            if body.get('channel_id'):
                vals['channel_id'] = int(body['channel_id'])

            # Set default stage (first non-fold stage of the team)
            if 'team_id' in vals:
                default_stage = request.env['helpdesk.stage'].sudo().search(
                    [('team_ids', 'in', [vals['team_id']]), ('fold', '=', False)],
                    order='sequence asc', limit=1,
                )
                if default_stage:
                    vals['stage_id'] = default_stage.id

            ticket = request.env['helpdesk.ticket'].sudo().create(vals)

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("create_helpdesk_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>/assign', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def assign_helpdesk_ticket(self, ticket_id, **kwargs):
        """POST /api/ticketing/tickets/{id}/assign — Assign ticket to a user."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            # Auth check: only CC creator or admin
            user = request.env.user
            if not user._is_admin() and ticket.create_uid.id != user.id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya pembuat tiket yang dapat melakukan assign ulang.'}, status=403
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            user_id = body.get('user_id')
            reason = (body.get('reason') or '').strip()
            if not user_id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'user_id wajib diisi.'}, status=400
                )

            # Store reason in context for model write to pick up
            old_user_id = ticket.user_id.id if ticket.user_id else None
            ticket.write({'user_id': int(user_id)})

            # Update assignment history with reason
            if reason:
                last_history = request.env['dke.ticket.assignment.history'].sudo().search([
                    ('ticket_id', '=', ticket_id),
                ], order='assigned_at desc', limit=1)
                if last_history:
                    last_history.write({'reason': reason})

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("assign_helpdesk_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>/message', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def post_ticket_message(self, ticket_id, **kwargs):
        """POST /api/ticketing/tickets/{id}/message — Post an internal note on the ticket."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
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
            msg = ticket.message_post(
                body=content,
                message_type='comment',
                subtype_xmlid='mail.mt_note',
                author_id=user.partner_id.id,
            )

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': msg.id,
                    'sender': user.name,
                    'sender_id': user.id,
                    'content': content,
                    'created_at': self._fmt_dt(msg.date),
                },
            })
        except Exception as e:
            _logger.error("post_ticket_message error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>/chat', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_ticket_chat(self, ticket_id, **kwargs):
        """GET /api/ticketing/tickets/{id}/chat — Get the linked chat room."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )
            if not ticket.channel_id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket ini belum memiliki chat room.'}, status=404
                )
            return request.make_json_response({
                'status': 'success',
                'data': self._room_to_dict(ticket.channel_id),
            })
        except Exception as e:
            _logger.error("get_ticket_chat error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>/move', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def move_ticket_stage(self, ticket_id, **kwargs):
        """POST /api/ticketing/tickets/{id}/move — Move ticket to a different stage."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            stage_id = body.get('stage_id')
            if not stage_id:
                return request.make_json_response(
                    {'status': 'error', 'message': 'stage_id wajib diisi.'}, status=400
                )

            stage = request.env['helpdesk.stage'].sudo().browse(int(stage_id))
            if not stage.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Stage tidak ditemukan.'}, status=404
                )

            vals = {'stage_id': stage.id}
            # If moving to a closing stage, record close_date
            if stage.fold and not ticket.close_date:
                vals['close_date'] = fields.Datetime.now()
            ticket.write(vals)

            # Bus notification if ticket has linked room
            if ticket.channel_id:
                self._notify_bus(
                    request.env,
                    'dke_ticket_room_%s' % ticket.channel_id.id,
                    'ticketing.ticket_updated',
                    {'ticket_id': ticket_id, 'stage': stage.name, 'stage_id': stage.id},
                )

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("move_ticket_stage error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>/kanban-state', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def toggle_kanban_state(self, ticket_id, **kwargs):
        """POST /api/ticketing/tickets/{id}/kanban-state — Cycle kanban_state: normal→done→blocked→normal."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            cycle = {'normal': 'done', 'done': 'blocked', 'blocked': 'normal'}
            current = ticket.kanban_state or 'normal'
            new_state = cycle.get(current, 'normal')
            ticket.write({'kanban_state': new_state})

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("toggle_kanban_state error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>', type='http', auth='user', methods=['PUT'], csrf=False, cors='*')
    def update_helpdesk_ticket(self, ticket_id, **post):
        """PUT /api/ticketing/tickets/{id} — Update ticket fields."""
        try:
            if not request.httprequest.data:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Missing JSON payload.'}, status=400
                )

            payload = json.loads(request.httprequest.data.decode('utf-8'))
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            vals = {}
            if 'subject' in payload:
                vals['name'] = payload['subject']
            if 'description' in payload:
                vals['description'] = payload['description']
            if 'priority' in payload:
                vals['priority'] = str(payload['priority'])
            if 'team_id' in payload:
                vals['team_id'] = int(payload['team_id'])
            if 'user_id' in payload:
                vals['user_id'] = int(payload['user_id'])
            if 'tag_ids' in payload:
                vals['tag_ids'] = [(6, 0, [int(t) for t in payload['tag_ids']])]
            if 'ticket_type_id' in payload:
                vals['ticket_type_id'] = int(payload['ticket_type_id'])

            if vals:
                ticket.write(vals)
                # Room migration + notification now handled by model write()

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("update_helpdesk_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tickets/<int:ticket_id>', type='http', auth='user', methods=['DELETE'], csrf=False, cors='*')
    def delete_helpdesk_ticket(self, ticket_id, **kwargs):
        """DELETE /api/ticketing/tickets/{id} — Delete a ticket."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            ticket.unlink()
            return request.make_json_response({
                'status': 'success',
                'message': 'Tiket berhasil dihapus.',
            })
        except Exception as e:
            _logger.error("delete_helpdesk_ticket error: %s", e, exc_info=True)
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

    @http.route('/api/users/experts', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_expert_staff(self, **kwargs):
        """GET /api/users/experts — List available expert staff.

        Optional filter: ?specialization=face_wash
        """
        try:
            specialization = kwargs.get('specialization')
            domain = [
                ('dke_role', '=', 'expert_staff'),
                ('active', '=', True),
            ]
            if specialization:
                domain.append(('dke_specialization', '=', specialization))

            experts = request.env['res.users'].sudo().search(domain)

            Ticket = request.env['helpdesk.ticket'].sudo()
            closed_stage_ids = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

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
                    'avg_resolution_message_count': e.avg_resolution_message_count or 0.0,
                    'total_tickets_resolved': e.total_tickets_resolved or 0,
                    'total_messages_sent': e.total_messages_sent or 0,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % e.id,
                    'open_tickets': Ticket.search_count([
                        ('user_id', '=', e.id),
                        ('stage_id', 'not in', closed_stage_ids),
                    ]),
                } for e in experts],
            })
        except Exception as e:
            _logger.error("get_expert_staff error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Expert Performance Dashboard (admin)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/performance/experts', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_expert_performance(self, **kwargs):
        """GET /api/performance/experts — Expert staff performance dashboard."""
        try:
            user = request.env.user
            role = user.dke_role or ''

            specialization = kwargs.get('specialization')
            sort_by = kwargs.get('sort_by', 'total_tickets_resolved')

            if role == 'expert_staff':
                domain = [('id', '=', user.id)]
            elif role in ('sales_manager', 'admin') or user._is_admin():
                domain = [('dke_role', '=', 'expert_staff')]
                if specialization:
                    domain.append(('dke_specialization', '=', specialization))
            else:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            experts = request.env['res.users'].sudo().search(domain)
            Ticket = request.env['helpdesk.ticket'].sudo()

            closed_stage_ids = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

            def _expert_perf(e):
                resolved = Ticket.search_count([
                    ('user_id', '=', e.id),
                    ('stage_id', 'in', closed_stage_ids),
                ])
                in_progress = Ticket.search_count([
                    ('user_id', '=', e.id),
                    ('stage_id', 'not in', closed_stage_ids),
                ])

                # SLA compliance rate
                total_sla = Ticket.search_count([
                    ('user_id', '=', e.id),
                    ('sla_status', 'in', ['reached', 'failed']),
                ])
                sla_reached = Ticket.search_count([
                    ('user_id', '=', e.id),
                    ('sla_status', '=', 'reached'),
                ])
                sla_rate = round((sla_reached / total_sla) * 100, 1) if total_sla > 0 else 100.0

                return {
                    'id': e.id,
                    'name': e.name,
                    'email': e.email or e.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % e.id,
                    'specialization': e.dke_specialization or '',
                    'specialization_label': self.SPECIALIZATION_LABELS.get(e.dke_specialization or '', ''),
                    'total_tickets_resolved': resolved,
                    'tickets_in_progress': in_progress,
                    'sla_compliance_rate': sla_rate,
                    'avg_resolution_time_hours': e.avg_resolution_time or 0.0,
                    'avg_rating': e.avg_rating or 0.0,
                    'total_messages_sent': e.total_messages_sent or 0,
                }

            result = [_expert_perf(e) for e in experts]

            valid_sorts = ('total_tickets_resolved', 'avg_resolution_time', 'avg_rating')
            if sort_by not in valid_sorts:
                sort_by = 'total_tickets_resolved'
            sort_key = {
                'total_tickets_resolved': lambda x: -x['total_tickets_resolved'],
                'avg_resolution_time': lambda x: x['avg_resolution_time_hours'],
                'avg_rating': lambda x: -x['avg_rating'],
            }[sort_by]
            result.sort(key=sort_key)

            return request.make_json_response({
                'status': 'success',
                'data': result,
            })
        except Exception as e:
            _logger.error("get_expert_performance error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/performance/me', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_my_performance(self, **kwargs):
        """GET /api/performance/me — Expert staff views their own performance metrics."""
        try:
            user = request.env.user
            if user.dke_role not in ('expert_staff', 'sales_manager') and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            Ticket = request.env['helpdesk.ticket'].sudo()
            closed_stage_ids = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

            resolved_count = Ticket.search_count([
                ('user_id', '=', user.id),
                ('stage_id', 'in', closed_stage_ids),
            ])
            in_progress = Ticket.search_count([
                ('user_id', '=', user.id),
                ('stage_id', 'not in', closed_stage_ids),
            ])

            # Recent closed tickets
            recent = Ticket.search([
                ('user_id', '=', user.id),
                ('stage_id', 'in', closed_stage_ids),
            ], limit=10, order='close_date desc')

            recent_list = [{
                'ticket_id': t.id,
                'subject': t.name,
                'closed_at': self._fmt_dt(t.close_date),
                'close_hours': t.close_hours or 0,
            } for t in recent]

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': user.id,
                    'name': user.name,
                    'specialization': user.dke_specialization or '',
                    'specialization_label': self.SPECIALIZATION_LABELS.get(user.dke_specialization or '', ''),
                    'total_tickets_resolved': resolved_count,
                    'tickets_in_progress': in_progress,
                    'avg_resolution_time_hours': user.avg_resolution_time or 0.0,
                    'avg_rating': user.avg_rating or 0.0,
                    'total_messages_sent': user.total_messages_sent or 0,
                    'recent_resolved': recent_list,
                },
            })
        except Exception as e:
            _logger.error("get_my_performance error: %s", e, exc_info=True)
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
                ('active', '=', True),
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

    @http.route('/api/performance/care-staff', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_care_staff_performance(self, **kwargs):
        """GET /api/performance/care-staff — CS performance metrics per agent."""
        try:
            user = request.env.user
            role = user.dke_role or ''

            if role not in ('sales_manager', 'admin', 'customer_care') and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            care_users = request.env['res.users'].sudo().search([
                ('dke_role', '=', 'customer_care'),
                ('active', '=', True),
            ])

            Ticket = request.env['helpdesk.ticket'].sudo()
            Message = request.env['dke.ticketing.message'].sudo()
            Room = request.env['dke.ticketing.room'].sudo()

            result = []
            for u in care_users:
                tickets_created = Ticket.search_count([('create_uid', '=', u.id)])
                active_chats = Room.search_count([
                    ('assigned_cs_id', '=', u.id),
                    ('state', '=', 'active'),
                ])
                total_messages = Message.search_count([
                    ('sender_id', '=', u.id),
                    ('sender_type', '=', 'cs'),
                ])

                # Avg response time from monitoring
                avg_resp = 0
                try:
                    mon = request.env['dke.ticketing.monitoring'].sudo().search(
                        [('user_id', '=', u.id)], limit=1,
                    )
                    avg_resp = mon.avg_response_time if mon else 0
                except Exception:
                    pass

                result.append({
                    'user_id': u.id,
                    'name': u.name,
                    'email': u.email or u.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % u.id,
                    'tickets_created': tickets_created,
                    'avg_response_time': avg_resp,
                    'total_messages_sent': total_messages,
                    'active_chats': active_chats,
                })

            result.sort(key=lambda x: -x['tickets_created'])

            return request.make_json_response({
                'status': 'success',
                'data': result,
            })
        except Exception as e:
            _logger.error("get_care_staff_performance error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Ticket stats (for CS dashboard)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/dashboard', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_ticketing_dashboard(self, **kwargs):
        """GET /api/ticketing/dashboard — Ticket statistics grouped by stage."""
        try:
            user = request.env.user
            role = user.dke_role or ''

            Ticket = request.env['helpdesk.ticket'].sudo()
            Stage = request.env['helpdesk.stage'].sudo()

            if role == 'expert_staff':
                base_domain = [('user_id', '=', user.id)]
            elif role == 'customer_care':
                base_domain = [('create_uid', '=', user.id)]
            else:
                base_domain = []

            stages = Stage.search([], order='sequence')
            stage_breakdown = {}
            for s in stages:
                count = Ticket.search_count(base_domain + [('stage_id', '=', s.id)])
                stage_breakdown[s.name] = count

            total = Ticket.search_count(base_domain)
            closed_stage_ids = stages.filtered('fold').ids
            closed = Ticket.search_count(base_domain + [('stage_id', 'in', closed_stage_ids)])
            open_count = total - closed
            overdue = Ticket.search_count(base_domain + [('sla_fail', '=', True)])

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'total': total,
                    'open': open_count,
                    'closed': closed,
                    'overdue': overdue,
                    'stage_breakdown': stage_breakdown,
                },
            })
        except Exception as e:
            _logger.error("get_ticketing_dashboard error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Helpdesk ticket detail
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/tickets/<int:ticket_id>', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_ticket_detail(self, ticket_id, **kwargs):
        """GET /api/ticketing/tickets/{id} — Single ticket detail."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )
            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("get_helpdesk_ticket_detail error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Helpdesk metadata (stages, teams, tags, categories)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/stages', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_stages(self, **kwargs):
        """GET /api/ticketing/stages — List all helpdesk stages."""
        try:
            stages = request.env['helpdesk.stage'].sudo().search([], order='sequence')
            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': s.id,
                    'name': s.name,
                    'sequence': s.sequence,
                    'fold': s.fold,
                    'is_close': s.fold,
                } for s in stages],
            })
        except Exception as e:
            _logger.error("get_helpdesk_stages error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/teams', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_teams(self, **kwargs):
        """GET /api/ticketing/teams — List all helpdesk teams."""
        try:
            teams = request.env['helpdesk.team'].sudo().search([])
            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': t.id,
                    'name': t.name,
                    'description': t.description or '',
                    'member_ids': t.member_ids.ids,
                    'use_sla': t.use_sla,
                } for t in teams],
            })
        except Exception as e:
            _logger.error("get_helpdesk_teams error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/tags', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_tags(self, **kwargs):
        """GET /api/ticketing/tags — List all helpdesk tags."""
        try:
            tags = request.env['helpdesk.tag'].sudo().search([])
            return request.make_json_response({
                'status': 'success',
                'data': [{'id': t.id, 'name': t.name} for t in tags],
            })
        except Exception as e:
            _logger.error("get_helpdesk_tags error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/categories', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_helpdesk_categories(self, **kwargs):
        """GET /api/ticketing/categories — List all ticket types (categories)."""
        try:
            types = request.env['helpdesk.ticket.type'].sudo().search([])
            return request.make_json_response({
                'status': 'success',
                'data': [{'id': t.id, 'name': t.name} for t in types],
            })
        except Exception as e:
            _logger.error("get_helpdesk_categories error: %s", e, exc_info=True)
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
                'delivered': True,
                'delivered_at': now,
                'created_at': now,
            })

            # Update attachment res_id
            attachment.sudo().write({'res_id': msg.id})

            room.sudo().write({'last_message_time': now})

            # Update ticket timestamps
            ticket = request.env['helpdesk.ticket'].sudo().search([('channel_id', '=', room_id)], limit=1)
            if ticket:
                ticket_vals = {'last_message_at': now}
                if user.dke_role == 'expert_staff' and not ticket.expert_first_response_at:
                    ticket_vals['expert_first_response_at'] = now
                ticket.write(ticket_vals)

            # Auto-assign if not already
            if not room.assigned_to:
                room.sudo().write({
                    'assigned_to': user.id,
                    'is_assigned': True,
                    'assigned_at': now,
                })

            # Bus notification
            self._notify_bus(
                request.env,
                'dke_ticket_room_%s' % room_id,
                'ticketing.new_message',
                {'room_id': room_id, 'message': self._message_to_dict(msg)},
            )

            return request.make_json_response({
                'status': 'success',
                'data': self._message_to_dict(msg),
            })
        except Exception as e:
            _logger.error("upload_media error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # All-staff dashboard (admin only)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/dashboard/all-staff', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_all_staff_dashboard(self, **kwargs):
        """GET /api/ticketing/dashboard/all-staff — Admin-only: all staff performance summary."""
        try:
            user = request.env.user
            if user.dke_role != 'admin' and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            Ticket = request.env['helpdesk.ticket'].sudo()
            Stage = request.env['helpdesk.stage'].sudo()
            Users = request.env['res.users'].sudo()

            closed_stage_ids = Stage.search([('fold', '=', True)]).ids

            # Expert staff performance
            experts = Users.search([('dke_role', '=', 'expert_staff'), ('active', '=', True)])
            expert_data = []
            total_expert_sla_rate = 0
            for e in experts:
                total = Ticket.search_count([('user_id', '=', e.id)])
                closed = Ticket.search_count([('user_id', '=', e.id), ('stage_id', 'in', closed_stage_ids)])
                open_count = total - closed
                overdue = Ticket.search_count([('user_id', '=', e.id), ('sla_fail', '=', True)])
                sla_rate = round(((total - overdue) / total * 100) if total else 0, 1)
                total_expert_sla_rate += sla_rate

                expert_data.append({
                    'user_id': e.id,
                    'name': e.name,
                    'email': e.email or e.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % e.id,
                    'specialization': e.dke_specialization or '',
                    'total': total,
                    'open': open_count,
                    'closed': closed,
                    'overdue': overdue,
                    'sla_success_rate': sla_rate,
                })

            # Customer care performance
            care_staff = Users.search([('dke_role', '=', 'customer_care'), ('active', '=', True)])
            care_data = []
            for c in care_staff:
                tickets_created = Ticket.search_count([('create_uid', '=', c.id)])
                care_data.append({
                    'user_id': c.id,
                    'name': c.name,
                    'email': c.email or c.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % c.id,
                    'tickets_created': tickets_created,
                })

            # Averages
            expert_count = len(expert_data)
            avg_sla_rate = round(total_expert_sla_rate / expert_count, 1) if expert_count else 0
            avg_overdue = round(sum(e['overdue'] for e in expert_data) / expert_count, 1) if expert_count else 0
            care_count = len(care_data)
            avg_tickets_created = round(sum(c['tickets_created'] for c in care_data) / care_count, 1) if care_count else 0

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'expert_staff': expert_data,
                    'customer_care': care_data,
                    'averages': {
                        'expert_sla_rate': avg_sla_rate,
                        'expert_overdue': avg_overdue,
                        'cs_tickets_created': avg_tickets_created,
                    },
                },
            })
        except Exception as e:
            _logger.error("get_all_staff_dashboard error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Resolve Ticket (PBI-38)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/tickets/<int:ticket_id>/resolve', type='http', auth='user', methods=['PUT'], csrf=False, cors='*')
    def resolve_ticket(self, ticket_id, **kwargs):
        """PUT /api/ticketing/tickets/{id}/resolve — Resolve a ticket (customer_care or admin only)."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            user = request.env.user
            role = getattr(user, 'dke_role', '')
            if not user._is_admin() and role != 'customer_care':
                return request.make_json_response(
                    {'status': 'error', 'message': 'Hanya Customer Care yang dapat menyelesaikan tiket ini.'}, status=403
                )

            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}
            resolution_notes = (body.get('resolution_notes') or '').strip()
            resolution_category = body.get('resolution_category', '')

            if not resolution_notes:
                return request.make_json_response(
                    {'status': 'error', 'message': 'resolution_notes wajib diisi.'}, status=400
                )

            valid_categories = (
                'product_quality', 'packaging_labeling', 'logistics_distribution',
                'stock_availability', 'regulation_certification', 'billing_payment',
                'special_request', 'other',
            )
            if resolution_category and resolution_category not in valid_categories:
                return request.make_json_response(
                    {'status': 'error', 'message': 'resolution_category tidak valid.'}, status=400
                )

            now = fields.Datetime.now()

            # Find Solved stage
            solved_stage = request.env['helpdesk.stage'].sudo().search([
                ('name', 'ilike', 'Solved'),
            ], limit=1)
            if not solved_stage:
                solved_stage = request.env['helpdesk.stage'].sudo().search([
                    ('fold', '=', True),
                ], order='sequence asc', limit=1)

            vals = {
                'resolution_notes': resolution_notes,
                'expert_resolved_at': now,
            }
            if resolution_category:
                vals['resolution_category'] = resolution_category
            if solved_stage:
                vals['stage_id'] = solved_stage.id
                if not ticket.close_date:
                    vals['close_date'] = now

            ticket.write(vals)

            # Recompute expert stats
            if ticket.user_id:
                ticket.user_id.sudo()._recompute_expert_stats()

            return request.make_json_response({
                'status': 'success',
                'data': self._ticket_to_dict(ticket),
            })
        except Exception as e:
            _logger.error("resolve_ticket error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Assignment History (PBI-18)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/tickets/<int:ticket_id>/assignment-history', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_assignment_history(self, ticket_id, **kwargs):
        """GET /api/ticketing/tickets/{id}/assignment-history — Assignment change log."""
        try:
            ticket = request.env['helpdesk.ticket'].sudo().browse(ticket_id)
            if not ticket.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Ticket tidak ditemukan.'}, status=404
                )

            history = request.env['dke.ticket.assignment.history'].sudo().search([
                ('ticket_id', '=', ticket_id),
            ], order='assigned_at desc')

            return request.make_json_response({
                'status': 'success',
                'data': [{
                    'id': h.id,
                    'assigned_from': h.assigned_from_id.name if h.assigned_from_id else None,
                    'assigned_from_id': h.assigned_from_id.id if h.assigned_from_id else None,
                    'assigned_to': h.assigned_to_id.name if h.assigned_to_id else None,
                    'assigned_to_id': h.assigned_to_id.id if h.assigned_to_id else None,
                    'assigned_by': h.assigned_by_id.name if h.assigned_by_id else None,
                    'assigned_by_id': h.assigned_by_id.id if h.assigned_by_id else None,
                    'reason': h.reason or '',
                    'assigned_at': self._fmt_dt(h.assigned_at),
                } for h in history],
            })
        except Exception as e:
            _logger.error("get_assignment_history error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Analytics — CC Performance (PBI-39)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/analytics/customer-care/performance', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_cc_analytics(self, **kwargs):
        """GET /api/analytics/customer-care/performance — CC performance with date filtering."""
        try:
            user = request.env.user
            if user.dke_role not in ('sales_manager',) and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            date_from = kwargs.get('date_from')
            date_to = kwargs.get('date_to')

            care_users = request.env['res.users'].sudo().search([
                ('dke_role', '=', 'customer_care'),
                ('active', '=', True),
            ])

            Ticket = request.env['helpdesk.ticket'].sudo()
            Room = request.env['dke.ticketing.room'].sudo()
            Message = request.env['dke.ticketing.message'].sudo()
            closed_stage_ids = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

            result = []
            for u in care_users:
                ticket_domain = [('create_uid', '=', u.id)]
                msg_domain = [('sender_id', '=', u.id), ('sender_type', '=', 'cs')]
                if date_from:
                    ticket_domain.append(('create_date', '>=', date_from))
                    msg_domain.append(('created_at', '>=', date_from))
                if date_to:
                    ticket_domain.append(('create_date', '<=', date_to))
                    msg_domain.append(('created_at', '<=', date_to))

                total_tickets = Ticket.search_count(ticket_domain)
                resolved_tickets = Ticket.search_count(ticket_domain + [('stage_id', 'in', closed_stage_ids)])
                total_messages = Message.search_count(msg_domain)
                active_chats = Room.search_count([
                    ('assigned_to', '=', u.id),
                    ('state', '=', 'active'),
                ])

                resolution_rate = round((resolved_tickets / total_tickets * 100), 1) if total_tickets > 0 else 0.0

                # Rating from sessions
                ratings = []
                rooms = Room.search([('assigned_to', '=', u.id)])
                for r in rooms:
                    for s in r.session_ids:
                        if s.customer_rating:
                            ratings.append(int(s.customer_rating))
                avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0

                result.append({
                    'user_id': u.id,
                    'name': u.name,
                    'email': u.email or u.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % u.id,
                    'total_chats_handled': total_tickets,
                    'total_messages_sent': total_messages,
                    'active_chats': active_chats,
                    'avg_rating': avg_rating,
                    'avg_response_time': u.avg_response_time or 0,
                    'resolution_rate': resolution_rate,
                })

            result.sort(key=lambda x: -x['total_chats_handled'])

            return request.make_json_response({
                'status': 'success',
                'data': result,
            })
        except Exception as e:
            _logger.error("get_cc_analytics error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Analytics — Expert Performance (PBI-45)
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/analytics/expert-staff/performance', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_expert_analytics(self, **kwargs):
        """GET /api/analytics/expert-staff/performance — Expert staff performance with date filtering."""
        try:
            user = request.env.user
            if user.dke_role not in ('sales_manager',) and not user._is_admin():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Akses ditolak.'}, status=403
                )

            date_from = kwargs.get('date_from')
            date_to = kwargs.get('date_to')

            experts = request.env['res.users'].sudo().search([
                ('dke_role', '=', 'expert_staff'),
                ('active', '=', True),
            ])

            Ticket = request.env['helpdesk.ticket'].sudo()
            closed_stage_ids = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

            result = []
            for e in experts:
                ticket_domain = [('user_id', '=', e.id)]
                if date_from:
                    ticket_domain.append(('create_date', '>=', date_from))
                if date_to:
                    ticket_domain.append(('create_date', '<=', date_to))

                total_tickets = Ticket.search_count(ticket_domain)
                resolved_tickets = Ticket.search_count(ticket_domain + [('stage_id', 'in', closed_stage_ids)])
                in_progress = total_tickets - resolved_tickets

                resolution_rate = round((resolved_tickets / total_tickets * 100), 1) if total_tickets > 0 else 0.0

                # Avg resolution time
                resolved = Ticket.search(ticket_domain + [('stage_id', 'in', closed_stage_ids)])
                avg_hours = 0.0
                if resolved:
                    avg_hours = round(sum(t.close_hours or 0 for t in resolved) / len(resolved), 2)

                # Rating
                ratings = []
                for t in resolved:
                    if t.channel_id:
                        for s in t.channel_id.session_ids:
                            if s.customer_rating:
                                ratings.append(int(s.customer_rating))
                avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0

                result.append({
                    'user_id': e.id,
                    'name': e.name,
                    'email': e.email or e.login,
                    'avatar_url': '/web/image/res.users/%d/avatar_128' % e.id,
                    'specialization': e.dke_specialization or '',
                    'specialization_label': self.SPECIALIZATION_LABELS.get(e.dke_specialization or '', ''),
                    'total_tickets': total_tickets,
                    'resolved': resolved_tickets,
                    'in_progress': in_progress,
                    'resolution_rate': resolution_rate,
                    'avg_resolution_time_hours': avg_hours,
                    'avg_rating': avg_rating,
                    'total_messages_sent': e.total_messages_sent or 0,
                })

            result.sort(key=lambda x: -x['resolved'])

            return request.make_json_response({
                'status': 'success',
                'data': result,
            })
        except Exception as e:
            _logger.error("get_expert_analytics error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # SLA Summary
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/sla-summary', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_sla_summary(self, **kwargs):
        """GET /api/ticketing/sla-summary — SLA policy overview with ticket counts."""
        try:
            SLA = request.env['helpdesk.sla'].sudo()
            Ticket = request.env['helpdesk.ticket'].sudo()
            sla_records = SLA.search([])

            policies = []
            totals = {'total': 0, 'reached': 0, 'failed': 0, 'ongoing': 0}

            for sla in sla_records:
                domain = [('sla_policy_ids', 'in', [sla.id])]
                total = Ticket.search_count(domain)
                reached = Ticket.search_count(domain + [('sla_reached', '=', True)])
                failed = Ticket.search_count(domain + [('sla_fail', '=', True)])
                ongoing = total - reached - failed

                policies.append({
                    'id': sla.id,
                    'name': sla.name,
                    'team': sla.team_id.name if sla.team_id else '',
                    'priority': sla.priority or '0',
                    'target_stage': sla.stage_id.name if sla.stage_id else '',
                    'time_hours': sla.time or 0,
                    'total': total,
                    'reached': reached,
                    'failed': failed,
                    'ongoing': ongoing,
                })
                totals['total'] += total
                totals['reached'] += reached
                totals['failed'] += failed
                totals['ongoing'] += ongoing

            return request.make_json_response({
                'status': 'success',
                'data': {'policies': policies, 'totals': totals},
            })
        except Exception as e:
            _logger.error("get_sla_summary error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ──────────────────────────────────────────────────────────────
    # Stage CRUD
    # ──────────────────────────────────────────────────────────────

    @http.route('/api/ticketing/stages', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_helpdesk_stage(self, **kwargs):
        """POST /api/ticketing/stages — Create a new helpdesk stage."""
        try:
            raw = request.httprequest.data
            body = json.loads(raw) if raw else {}

            name = (body.get('name') or '').strip()
            if not name:
                return request.make_json_response(
                    {'status': 'error', 'message': 'name wajib diisi.'}, status=400
                )

            vals = {
                'name': name,
                'sequence': int(body.get('sequence', 10)),
                'fold': bool(body.get('fold', False)),
            }

            # Link to team(s) if provided
            if body.get('team_id'):
                vals['team_ids'] = [(4, int(body['team_id']))]

            stage = request.env['helpdesk.stage'].sudo().create(vals)

            return request.make_json_response({
                'status': 'success',
                'data': {
                    'id': stage.id,
                    'name': stage.name,
                    'sequence': stage.sequence,
                    'fold': stage.fold,
                },
            })
        except Exception as e:
            _logger.error("create_helpdesk_stage error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    @http.route('/api/ticketing/stages/<int:stage_id>', type='http', auth='user', methods=['DELETE'], csrf=False, cors='*')
    def delete_helpdesk_stage(self, stage_id, **kwargs):
        """DELETE /api/ticketing/stages/{id} — Delete a helpdesk stage."""
        try:
            stage = request.env['helpdesk.stage'].sudo().browse(stage_id)
            if not stage.exists():
                return request.make_json_response(
                    {'status': 'error', 'message': 'Stage tidak ditemukan.'}, status=404
                )

            # Check for tickets in this stage
            ticket_count = request.env['helpdesk.ticket'].sudo().search_count(
                [('stage_id', '=', stage_id)]
            )
            if ticket_count > 0:
                return request.make_json_response(
                    {'status': 'error', 'message': 'Tidak dapat menghapus stage yang masih memiliki %d tiket.' % ticket_count}, status=400
                )

            stage.unlink()
            return request.make_json_response({
                'status': 'success',
                'message': 'Stage berhasil dihapus.',
            })
        except Exception as e:
            _logger.error("delete_helpdesk_stage error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ═══════════════════════════════════════════════════
    # Customer Search
    # ═══════════════════════════════════════════════════

    @http.route('/api/ticketing/customers/search', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def search_customers(self, **kwargs):
        """GET /api/ticketing/customers/search?q=query — Search res.partner by name/email/phone."""
        try:
            query = kwargs.get('q', '').strip()
            limit = min(int(kwargs.get('limit', 20)), 50)

            if len(query) < 2:
                return request.make_json_response({
                    'status': 'success',
                    'data': [],
                })

            Partner = request.env['res.partner'].sudo()
            domain = [
                '|', '|',
                ('name', 'ilike', query),
                ('email', 'ilike', query),
                ('phone', 'ilike', query),
            ]
            partners = Partner.search(domain, limit=limit, order='name asc')

            # Check which partners are DKE customers (have ticketing rooms)
            Room = request.env['dke.ticketing.room'].sudo()
            result = []
            for p in partners:
                is_dke = bool(Room.search_count([('partner_id', '=', p.id)]))
                result.append({
                    'id': p.id,
                    'name': p.name or '',
                    'email': p.email or '',
                    'phone': p.phone or '',
                    'is_dke_customer': is_dke,
                })

            return request.make_json_response({
                'status': 'success',
                'data': result,
            })
        except Exception as e:
            _logger.error("search_customers error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )

    # ═══════════════════════════════════════════════════
    # Personal Dashboard Stats
    # ═══════════════════════════════════════════════════

    @http.route('/api/ticketing/dashboard/me', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_my_dashboard_stats(self, **kwargs):
        """GET /api/ticketing/dashboard/me — Personal performance stats (role-aware)."""
        try:
            user = request.env.user
            uid = user.id

            # Determine role
            is_expert = user.has_group('dke_ticketing.group_expert_staff')
            is_cs = user.has_group('dke_ticketing.group_customer_care')

            Ticket = request.env['helpdesk.ticket'].sudo()
            Message = request.env['dke.ticketing.message'].sudo()
            Room = request.env['dke.ticketing.room'].sudo()

            stats = {}

            if is_cs:
                # Tickets created by this CS
                stats['tickets_created'] = Ticket.search_count([('create_uid', '=', uid)])

                # Active chats assigned to this CS
                stats['active_chats'] = Room.search_count([
                    ('assigned_cs_id', '=', uid),
                    ('state', '=', 'active'),
                ])

                # Total messages sent
                stats['total_messages_sent'] = Message.search_count([
                    ('sender_id', '=', uid),
                    ('sender_type', '=', 'cs'),
                ])

                # Avg response time (from monitoring model if available)
                try:
                    Monitoring = request.env['dke.ticketing.monitoring'].sudo()
                    mon = Monitoring.search([('user_id', '=', uid)], limit=1)
                    stats['avg_response_time'] = mon.avg_response_time if mon else 0
                except Exception:
                    stats['avg_response_time'] = 0

            if is_expert:
                # Tickets resolved (in fold/close stages assigned to this expert)
                fold_stages = request.env['helpdesk.stage'].sudo().search([('fold', '=', True)])
                fold_ids = fold_stages.ids if fold_stages else []

                stats['tickets_resolved'] = Ticket.search_count([
                    ('user_id', '=', uid),
                    ('stage_id', 'in', fold_ids),
                ]) if fold_ids else 0

                # Tickets in progress (assigned, not in fold stage)
                stats['tickets_in_progress'] = Ticket.search_count([
                    ('user_id', '=', uid),
                    ('stage_id', 'not in', fold_ids),
                ])

                # SLA compliance rate
                total_with_sla = Ticket.search_count([
                    ('user_id', '=', uid),
                    ('sla_status', 'in', ['reached', 'failed']),
                ])
                reached = Ticket.search_count([
                    ('user_id', '=', uid),
                    ('sla_status', '=', 'reached'),
                ])
                stats['sla_compliance_rate'] = round((reached / total_with_sla) * 100, 1) if total_with_sla > 0 else 100.0

                # Avg resolution time & rating from monitoring
                try:
                    Monitoring = request.env['dke.ticketing.monitoring'].sudo()
                    mon = Monitoring.search([('user_id', '=', uid)], limit=1)
                    stats['avg_resolution_time'] = mon.avg_resolution_time if mon else 0
                    stats['avg_rating'] = mon.avg_rating if mon else 0
                except Exception:
                    stats['avg_resolution_time'] = 0
                    stats['avg_rating'] = 0

                # Total messages sent
                stats['total_messages_sent'] = Message.search_count([
                    ('sender_id', '=', uid),
                ])

            return request.make_json_response({
                'status': 'success',
                'data': stats,
            })
        except Exception as e:
            _logger.error("get_my_dashboard_stats error: %s", e, exc_info=True)
            return request.make_json_response(
                {'status': 'error', 'message': str(e)}, status=500
            )
