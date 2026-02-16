# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import json


class ChatController(http.Controller):
    """REST API endpoints for Chat system.

    EPIC01 - PBI-2, PBI-3, PBI-4, PBI-6
    EPIC02 - PBI-8
    """

    @http.route('/api/chat/list', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_chat_list(self, **kwargs):
        """GET /api/chat/list — List all chat rooms with pagination.

        PBI-2: Returns array of conversations sorted by last_message_time desc.
        Query Params: page, limit (default 20)
        """
        # TODO: Implement pagination and filtering
        return request.make_json_response({'status': 'ok', 'data': []})

    @http.route('/api/chat/rooms/<int:room_id>/messages', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_room_messages(self, room_id, **kwargs):
        """GET /api/chat/rooms/{room_id}/messages — Get message history.

        PBI-3: Returns all messages for a room, marks as read.
        """
        # TODO: Implement message retrieval and mark-as-read
        return request.make_json_response({'status': 'ok', 'data': []})

    @http.route('/api/chat/rooms/<int:room_id>/reply', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def reply_to_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/reply — Send reply message.

        PBI-4: Validates input, sends via Marketplace API, saves to DB.
        Request Body: { "message": "...", "type": "text" }
        """
        # TODO: Implement message sending with marketplace integration
        return request.make_json_response({'status': 'ok'})

    @http.route('/api/chat/rooms/<int:room_id>/close', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def close_chat(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/close — Archive/close chat.

        PBI-6: Marks chat as done, moves to history.
        """
        # TODO: Implement chat closing
        return request.make_json_response({'status': 'ok'})

    @http.route('/api/chat/rooms/<int:room_id>/schedule', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def schedule_message(self, room_id, **kwargs):
        """POST /api/chat/rooms/{room_id}/schedule — Schedule message.

        PBI-8 (EPIC02): Saves scheduled message with send_at time.
        Request Body: { "message": "...", "send_at": "2026-03-01 09:00:00" }
        """
        # TODO: Implement scheduled message creation
        return request.make_json_response({'status': 'ok'})
