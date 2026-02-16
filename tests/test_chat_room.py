# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase


class TestChatRoom(TransactionCase):
    """Test cases for dke.chat.room model.

    EPIC01 - PBI-1, PBI-2, PBI-6
    """

    def setUp(self):
        super().setUp()
        # TODO: Create test data
        self.ChatRoom = self.env['dke.chat.room']

    def test_create_chat_room(self):
        """Test creating a basic chat room."""
        room = self.ChatRoom.create({
            'name': 'Test Chat Room',
            'customer_name': 'Test Customer',
            'source': 'shopee',
        })
        self.assertEqual(room.state, 'active')
        self.assertEqual(room.unread_count, 0)

    def test_chat_room_state_transitions(self):
        """Test chat room can transition between states."""
        # TODO: Implement state transition tests
        pass

    def test_chat_room_message_count(self):
        """Test message counting on chat room."""
        # TODO: Implement message counting tests
        pass
