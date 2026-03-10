# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase


class TestTicketingRoom(TransactionCase):
    """Test cases for dke.ticketing.room model.

    EPIC01 - PBI-1, PBI-2, PBI-6
    """

    def setUp(self):
        super().setUp()
        # TODO: Create test data
        self.TicketingRoom = self.env['dke.ticketing.room']

    def test_create_ticketing_room(self):
        """Test creating a basic chat room."""
        room = self.TicketingRoom.create({
            'name': 'Test Chat Room',
            'customer_name': 'Test Customer',
            'source': 'shopee',
        })
        self.assertEqual(room.state, 'active')
        self.assertEqual(room.unread_count, 0)

    def test_ticketing_room_state_transitions(self):
        """Test chat room can transition between states."""
        # TODO: Implement state transition tests
        pass

    def test_ticketing_room_message_count(self):
        """Test message counting on chat room."""
        # TODO: Implement message counting tests
        pass
