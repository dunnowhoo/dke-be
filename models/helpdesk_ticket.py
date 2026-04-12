# -*- coding: utf-8 -*-

import threading
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

# Anti-recursion guard: prevents ticket.create → room.create → ticket.create loop
_creating_room = threading.local()


class HelpdeskTicketExt(models.Model):
    """Extend helpdesk.ticket with DKE-specific fields."""

    _inherit = 'helpdesk.ticket'

    # Link to ticketing room (chat bubble)
    channel_id = fields.Many2one(
        'dke.ticketing.room',
        string='Chat Room',
        ondelete='set null',
    )
    resolution_notes = fields.Html(string='Resolution Notes')

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        # Skip auto-room creation if called from TicketingRoom.create()
        if getattr(_creating_room, 'active', False):
            return tickets
        for ticket in tickets:
            if not ticket.channel_id:
                # Auto-create a linked ticketing room (with anti-recursion guard)
                from .ticketing_room import _creating_ticket
                _creating_ticket.active = True
                try:
                    room = self.env['dke.ticketing.room'].sudo().create({
                        'name': ticket.name or ticket.ticket_ref or 'Ticket',
                        'customer_name': ticket.partner_name or (ticket.partner_id.name if ticket.partner_id else ''),
                        'customer_phone': ticket.partner_phone or '',
                        'source': 'platform',
                        'state': 'active',
                    })
                    ticket.channel_id = room.id
                finally:
                    _creating_ticket.active = False
        return tickets

    def write(self, vals):
        # Capture old user_id per ticket BEFORE super().write() changes it
        old_user_map = {}
        if 'user_id' in vals:
            for ticket in self:
                old_user_map[ticket.id] = ticket.user_id.id if ticket.user_id else None

        res = super().write(vals)

        # Handle user_id (assigned expert) change: room migration + notification
        if 'user_id' in vals and vals['user_id']:
            new_user_id = vals['user_id']
            new_user = self.env['res.users'].sudo().browse(new_user_id)
            now = fields.Datetime.now()

            for ticket in self:
                old_uid = old_user_map.get(ticket.id)
                if new_user_id == old_uid:
                    continue  # No actual change

                # ── Room migration ──
                # Archive old room
                old_room = ticket.channel_id
                if old_room:
                    old_room.sudo().write({'state': 'archived'})

                # Create new room for the new expert (with anti-recursion guard)
                from .ticketing_room import _creating_ticket
                _creating_ticket.active = True
                try:
                    room_name = '%s_%s' % (ticket.name or 'Ticket', new_user_id)
                    new_room = self.env['dke.ticketing.room'].sudo().create({
                        'name': room_name,
                        'customer_name': ticket.partner_name or (ticket.partner_id.name if ticket.partner_id else ''),
                        'customer_phone': ticket.partner_phone or '',
                        'source': 'platform',
                        'state': 'active',
                        'assigned_to': new_user_id,
                        'is_assigned': True,
                        'assigned_at': now,
                        'last_message_time': now,
                    })
                finally:
                    _creating_ticket.active = False

                # Point ticket to new room (use super to avoid recursion)
                super(HelpdeskTicketExt, ticket).write({'channel_id': new_room.id})

                # Create session for new room
                self.env['dke.ticketing.session'].sudo().create({
                    'room_id': new_room.id,
                    'cs_user_id': new_user_id,
                    'state': 'active',
                })

                # System message in new room
                self.env['dke.ticketing.message'].sudo().create({
                    'room_id': new_room.id,
                    'sender_type': 'system',
                    'content_text': 'Tiket dialihkan ke %s' % (new_user.name or ''),
                    'message_type': 'text',
                    'send_status': 'sent',
                    'created_at': now,
                })

                # ── Notification ──
                self.env['dke.notification'].sudo().create({
                    'user_id': new_user_id,
                    'title': 'Tiket Baru Ditugaskan',
                    'message': 'Anda ditugaskan untuk menangani tiket %s: %s' % (
                        ticket.ticket_ref or ticket.id, ticket.name,
                    ),
                    'notification_type': 'ticket_assigned',
                    'reference_model': 'helpdesk.ticket',
                    'reference_id': ticket.id,
                })

        # ── Stage cascade: closing ticket → close linked room ──
        if 'stage_id' in vals and not self.env.context.get('skip_room_cascade'):
            new_stage = self.env['helpdesk.stage'].sudo().browse(vals['stage_id'])
            if new_stage.exists() and new_stage.fold:
                for ticket in self:
                    room = ticket.channel_id
                    if room and room.state == 'active':
                        room.sudo().with_context(skip_ticket_cascade=True).write({
                            'state': 'done',
                        })
                        active_session = room.get_active_session()
                        if active_session:
                            active_session.action_close()

        return res

    def unlink(self):
        """Archive linked rooms before deleting tickets."""
        for ticket in self:
            room = ticket.channel_id
            if room:
                room.sudo().with_context(skip_ticket_cascade=True).write({
                    'state': 'archived',
                })
                active_session = room.get_active_session()
                if active_session:
                    active_session.action_close()
        return super().unlink()
