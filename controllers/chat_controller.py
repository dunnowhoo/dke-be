# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
from datetime import datetime
import json
import logging

_logger = logging.getLogger(__name__)


class ChatController(http.Controller):
    """REST API endpoints for Chat system.

    EPIC01 - PBI-2, PBI-3, PBI-4, PBI-6
    EPIC02 - PBI-8
    """

    # ──────────────────────────────────────────────
    # LIST CHAT ROOMS
    # ──────────────────────────────────────────────
    @http.route('/api/chat/list', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_chat_list(self, **kwargs):
        """GET /api/chat/list — List all chat rooms with pagination and filter.

        PBI-2: Returns array of conversations sorted by last_message_time desc.
        Query Params:
            - page (int, default 1)
            - limit (int, default 20)
            - state (str, optional: 'active', 'done', 'archived')
        """
        try:
            page = int(kwargs.get('page', 1))
            limit = int(kwargs.get('limit', 20))
            state_filter = kwargs.get('state', '')
            offset = (page - 1) * limit

            domain = []
            if state_filter and state_filter in ('active', 'done', 'archived'):
                domain.append(('state', '=', state_filter))

            ChatRoom = request.env['dke.chat.room'].sudo()
            total = ChatRoom.search_count(domain)
            rooms = ChatRoom.search(domain, limit=limit, offset=offset, order='last_message_time desc')

            # Get last message preview for each room
            data = []
            for room in rooms:
                room_dict = room.to_dict()
                last_msg = room.message_ids.sorted('created_at', reverse=True)[:1]
                room_dict['last_message_preview'] = last_msg.content_text[:80] if last_msg else ''
                room_dict['last_message_sender_type'] = last_msg.sender_type if last_msg else ''
                data.append(room_dict)

            return request.make_json_response({
                'status': 'ok',
                'data': data,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total': total,
                    'total_pages': (total + limit - 1) // limit,
                }
            })
        except Exception as e:
            _logger.error("Error in get_chat_list: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # GET ROOM MESSAGES
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/messages', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_messages(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}/messages — Get message history.

        PBI-3: Returns all messages for a room, marks as read.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            # Mark unread messages as read
            unread = room.message_ids.filtered(lambda m: not m.is_read)
            if unread:
                unread.write({'is_read': True})
                room.write({'unread_count': 0})

            messages = [msg.to_dict() for msg in room.message_ids.sorted('created_at')]

            return request.make_json_response({
                'status': 'ok',
                'data': messages,
                'room': room.to_dict(),
            })
        except Exception as e:
            _logger.error("Error in get_room_messages: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # SEND REPLY
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/reply', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reply_to_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/reply — Send reply message.

        PBI-4: Validates input, saves to DB.
        Request Body: { "message": "...", "type": "text" }
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            body = json.loads(request.httprequest.data or '{}')
            message_text = body.get('message', '').strip()
            msg_type = body.get('type', 'text')

            if not message_text:
                return request.make_json_response({
                    'status': 'error', 'error': 'Message cannot be empty'
                }, status=400)

            current_user = request.env.user
            now = datetime.now()

            # Auto-assign CS if not assigned
            if not room.assigned_care_id:
                room.write({'assigned_care_id': current_user.id})

            # Get or create active session
            active_session = room.get_active_session()
            if not active_session:
                active_session = request.env['dke.chat.session'].sudo().create({
                    'room_id': room.id,
                    'cs_user_id': current_user.id,
                    'state': 'active',
                })

            # Create the message
            new_msg = request.env['dke.chat.message'].sudo().create({
                'room_id': room.id,
                'session_id': active_session.id,
                'sender_type': 'cs',
                'sender_id': current_user.id,
                'agent_name': current_user.name,
                'content_text': message_text,
                'message_type': msg_type,
                'is_read': True,
                'send_status': 'sent',
            })

            # Update room last message time
            room.write({'last_message_time': now})

            return request.make_json_response({
                'status': 'ok',
                'data': new_msg.to_dict(),
            })
        except Exception as e:
            _logger.error("Error in reply_to_chat: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # CLOSE CHAT SESSION
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/close', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def close_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/close — Close/archive chat.

        PBI-6: Marks chat as done, closes active session.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            # Close active session
            active_session = room.get_active_session()
            if active_session:
                active_session.action_close()

            room.write({'state': 'done'})

            return request.make_json_response({
                'status': 'ok',
                'data': room.to_dict(),
            })
        except Exception as e:
            _logger.error("Error in close_chat: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # ASSIGN / TAKEOVER CS
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/assign', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def assign_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/assign — Assign or takeover.

        Auto-assigns the current user as CS for this room.
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            current_user = request.env.user
            room.write({'assigned_care_id': current_user.id})

            # Update session CS
            active_session = room.get_active_session()
            if active_session:
                active_session.write({'cs_user_id': current_user.id})

            return request.make_json_response({
                'status': 'ok',
                'data': room.to_dict(),
            })
        except Exception as e:
            _logger.error("Error in assign_chat: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # RATE SESSION
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/rate', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def rate_session(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/rate — Submit customer rating.

        Request Body: { "rating": "5", "feedback": "Great service!" }
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            body = json.loads(request.httprequest.data or '{}')
            rating = body.get('rating', '')
            feedback = body.get('feedback', '')

            if rating not in ('1', '2', '3', '4', '5'):
                return request.make_json_response({
                    'status': 'error', 'error': 'Rating must be 1-5'
                }, status=400)

            # Find latest session (active or recently closed)
            session = room.session_ids.sorted('started_at', reverse=True)[:1]
            if session:
                session.write({
                    'customer_rating': rating,
                    'customer_feedback': feedback,
                })

            return request.make_json_response({
                'status': 'ok',
                'message': 'Rating submitted',
            })
        except Exception as e:
            _logger.error("Error in rate_session: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # SCHEDULE MESSAGE
    # ──────────────────────────────────────────────
    @http.route('/api/chat/rooms/<int:room_id>/schedule', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def schedule_message(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/schedule — Schedule message.

        PBI-8 (EPIC02): Saves scheduled message with send_at time.
        Request Body: { "message": "...", "send_at": "2026-03-01 09:00:00" }
        """
        try:
            room = request.env['dke.chat.room'].sudo().browse(room_id)
            if not room.exists():
                return request.make_json_response({
                    'status': 'error', 'error': 'Room not found'
                }, status=404)

            body = json.loads(request.httprequest.data or '{}')
            message_text = body.get('message', '').strip()
            send_at = body.get('send_at', '')

            if not message_text or not send_at:
                return request.make_json_response({
                    'status': 'error', 'error': 'message and send_at are required'
                }, status=400)

            scheduled = request.env['dke.scheduled.message'].sudo().create({
                'room_id': room.id,
                'content': message_text,
                'send_at': send_at,
                'state': 'pending',
            })

            return request.make_json_response({
                'status': 'ok',
                'data': {'id': scheduled.id},
            })
        except Exception as e:
            _logger.error("Error in schedule_message: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)

    # ──────────────────────────────────────────────
    # SEED DEMO DATA (Development Only)
    # ──────────────────────────────────────────────
    @http.route('/api/chat/seed', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def seed_demo_data(self, **kwargs):
        """POST /api/chat/seed — Seed demo chat data for development.

        Creates sample chat rooms, messages, and sessions.
        """
        try:
            ChatRoom = request.env['dke.chat.room'].sudo()
            ChatMessage = request.env['dke.chat.message'].sudo()
            ChatSession = request.env['dke.chat.session'].sudo()
            current_user = request.env.user

            # Check if data already seeded
            existing = ChatRoom.search_count([])
            if existing > 0:
                return request.make_json_response({
                    'status': 'ok',
                    'message': f'Data already exists ({existing} rooms). Skipping seed.',
                })

            rooms_data = [
                {
                    'name': 'Rina Kusuma',
                    'customer_name': 'Rina Kusuma',
                    'customer_phone': '+62 812-3456-7890',
                    'source': 'whatsapp',
                    'state': 'active',
                    'assigned_care_id': current_user.id,
                    'last_message_time': '2026-02-28 07:20:00',
                    'messages': [
                        {'sender_type': 'customer', 'content_text': 'Halo min, mau tanya. Serum anti-aging nya aman buat kulit sensitif gak? Kandungan utamanya apa?', 'created_at': '2026-02-28 07:10:00'},
                        {'sender_type': 'ai', 'agent_name': 'Salestial Agent', 'content_text': 'Halo Kak Rina! 👋 Aman banget. Kandungan utamanya Centella Asiatica & Ceramide yang menenangkan kulit kemerahan. Sudah BPOM juga ya. 🌿', 'created_at': '2026-02-28 07:11:00'},
                        {'sender_type': 'cs', 'agent_name': current_user.name, 'content_text': 'Menambahkan info dari AI di atas, serum ini juga bebas alkohol kak, jadi benar-benar aman untuk kulit yang sangat reaktif. Mau dibantu kirim hari ini?', 'created_at': '2026-02-28 07:12:00'},
                        {'sender_type': 'customer', 'content_text': 'Hmm oke. Tapi saya ada riwayat alergi parah sama Phenoxyethanol. Bisa tolong dicek detail persentasenya? Mau mastiin sama orang farmasi langsung.', 'created_at': '2026-02-28 07:15:00'},
                        {'sender_type': 'cs', 'agent_name': current_user.name, 'content_text': 'Baik Kak Rina, saya bantu cek ke tim internal kami ya. Tunggu sebentar.', 'created_at': '2026-02-28 07:16:00'},
                        {'sender_type': 'cs', 'agent_name': current_user.name, 'content_text': 'Setelah saya cek, kadar Phenoxyethanol hanya 0.1%, sangat jauh di bawah batas aman BPOM (1%). Apakah kadar ini masih masuk toleransi kakak?', 'created_at': '2026-02-28 07:18:00'},
                        {'sender_type': 'customer', 'content_text': 'Wah makasih min responnya cepet banget. Kayaknya 0.1% masih aman sih buat kulit saya.', 'created_at': '2026-02-28 07:20:00'},
                    ]
                },
                {
                    'name': 'Shopee Chat',
                    'customer_name': 'Shopee Chat',
                    'customer_phone': 'rina_shopee_id',
                    'source': 'shopee',
                    'state': 'active',
                    'assigned_care_id': False,
                    'last_message_time': '2026-02-28 05:05:00',
                    'messages': [
                        {'sender_type': 'customer', 'content_text': 'Permisi min, pesanan saya #99102 kok belum di pick up kurir ya?', 'created_at': '2026-02-28 05:00:00'},
                        {'sender_type': 'customer', 'content_text': 'Padahal estimasi sampainya besok.', 'created_at': '2026-02-28 05:02:00'},
                        {'sender_type': 'customer', 'content_text': 'Tolong bantuannya segera ya min, penting bgt soalnya.', 'created_at': '2026-02-28 05:05:00'},
                    ]
                },
                {
                    'name': 'Lina Wijaya',
                    'customer_name': 'Lina Wijaya',
                    'customer_phone': '+62 855-1212-3344',
                    'source': 'whatsapp',
                    'state': 'active',
                    'last_message_time': '2026-02-28 03:45:00',
                    'messages': [
                        {'sender_type': 'customer', 'content_text': 'Min, paket saya keterangannya sudah sampai tapi saya belum terima barangnya.', 'created_at': '2026-02-28 03:30:00'},
                        {'sender_type': 'ai', 'agent_name': 'Salestial Agent', 'content_text': 'Mohon maaf atas ketidaknyamanannya Kak Lina. AI mendeteksi status pengiriman Anda memang sudah Delivered. Silakan tunggu respon CS kami.', 'created_at': '2026-02-28 03:31:00'},
                        {'sender_type': 'cs', 'agent_name': 'CS Budi', 'content_text': 'Halo Kak Lina, saya Budi. Sedang saya koordinasikan dengan pihak kurir ya terkait posisi paket terakhir.', 'created_at': '2026-02-28 03:35:00'},
                        {'sender_type': 'customer', 'content_text': 'Ok min, tolong banget ya soalnya ini isinya obat penting.', 'created_at': '2026-02-28 03:40:00'},
                        {'sender_type': 'cs', 'agent_name': 'CS Budi', 'content_text': 'Siap kak, akan segera saya kabari jika ada update terbaru.', 'created_at': '2026-02-28 03:45:00'},
                    ]
                },
                {
                    'name': 'Zahra Putri',
                    'customer_name': 'Zahra Putri',
                    'customer_phone': 'zahra_shop88',
                    'source': 'shopee',
                    'state': 'done',
                    'last_message_time': '2026-02-27 12:00:00',
                    'messages': [
                        {'sender_type': 'customer', 'content_text': 'Min, ada promo beli 2 gratis 1 gak bulan ini?', 'created_at': '2026-02-27 10:00:00'},
                        {'sender_type': 'ai', 'agent_name': 'Salestial Agent', 'content_text': 'Halo Kak Zahra! Untuk saat ini promo tersebut sudah berakhir per tanggal 15 kemarin kak. 😊', 'created_at': '2026-02-27 10:01:00'},
                        {'sender_type': 'cs', 'agent_name': 'CS Sarah', 'content_text': 'Namun kami ada voucher diskon 10% khusus untuk member Shopee nih kak sebagai gantinya. Tertarik?', 'created_at': '2026-02-27 10:05:00'},
                        {'sender_type': 'customer', 'content_text': 'Oalah gitu ya. Boleh deh min voucher nya gimana cara klaimnya?', 'created_at': '2026-02-27 10:10:00'},
                        {'sender_type': 'cs', 'agent_name': 'CS Sarah', 'content_text': 'Tinggal masukkan kode promo: BEAUTY10 saat checkout ya kak. Promo terbatas lho!', 'created_at': '2026-02-27 11:00:00'},
                        {'sender_type': 'customer', 'content_text': 'Oke min, sudah saya klaim. Makasih banyak infonya.', 'created_at': '2026-02-27 11:30:00'},
                        {'sender_type': 'cs', 'agent_name': 'CS Sarah', 'content_text': 'Sama-sama Kak Zahra! Senang bisa membantu. Ada lagi yang bisa saya bantu?', 'created_at': '2026-02-27 12:00:00'},
                    ]
                },
                {
                    'name': 'Budi Santoso',
                    'customer_name': 'Budi Santoso',
                    'customer_phone': 'budi_santoso_official',
                    'source': 'shopee',
                    'state': 'done',
                    'assigned_care_id': current_user.id,
                    'last_message_time': '2026-02-17 10:00:00',
                    'messages': [
                        {'sender_type': 'customer', 'content_text': 'Kak, krim siang yang SPF 50 ready stok?', 'created_at': '2026-02-17 08:00:00'},
                        {'sender_type': 'ai', 'agent_name': 'Salestial Agent', 'content_text': 'Ready Kak Budi! Langsung di checkout aja mumpung stok baru masuk pagi ini.', 'created_at': '2026-02-17 08:01:00'},
                        {'sender_type': 'cs', 'agent_name': current_user.name, 'content_text': 'Iya kak, stok tinggal sisa 5 pcs saja nih. Mending buruan di checkout kak sebelum kehabisan.', 'created_at': '2026-02-17 08:05:00'},
                        {'sender_type': 'customer', 'content_text': 'Oke min, sudah saya bayar ya barusan.', 'created_at': '2026-02-17 09:00:00'},
                        {'sender_type': 'cs', 'agent_name': current_user.name, 'content_text': 'Siap Kak Budi! Pesanan sudah masuk dan akan segera kami proses untuk pengiriman sore ini ya.', 'created_at': '2026-02-17 09:30:00'},
                        {'sender_type': 'customer', 'content_text': 'Makasih min.', 'created_at': '2026-02-17 10:00:00'},
                    ]
                },
            ]

            created_rooms = []
            for room_data in rooms_data:
                messages = room_data.pop('messages', [])
                # Handle False assigned_care_id
                if room_data.get('assigned_care_id') is False:
                    room_data.pop('assigned_care_id', None)

                room = ChatRoom.create(room_data)

                # Create session for active rooms with assigned CS
                session = False
                if room.state == 'active' and room.assigned_care_id:
                    session = ChatSession.create({
                        'room_id': room.id,
                        'cs_user_id': room.assigned_care_id.id,
                        'state': 'active',
                    })
                elif room.state == 'done':
                    session = ChatSession.create({
                        'room_id': room.id,
                        'cs_user_id': room.assigned_care_id.id if room.assigned_care_id else current_user.id,
                        'state': 'closed',
                        'ended_at': room.last_message_time,
                        'customer_rating': '5',
                    })

                for msg in messages:
                    msg['room_id'] = room.id
                    if session:
                        msg['session_id'] = session.id
                    if msg['sender_type'] == 'cs':
                        msg['sender_id'] = current_user.id
                    ChatMessage.create(msg)

                created_rooms.append(room.name)

            return request.make_json_response({
                'status': 'ok',
                'message': f'Seeded {len(created_rooms)} chat rooms',
                'rooms': created_rooms,
            })
        except Exception as e:
            _logger.error("Error in seed_demo_data: %s", str(e))
            return request.make_json_response({
                'status': 'error', 'error': str(e)
            }, status=500)
