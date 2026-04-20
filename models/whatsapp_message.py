# -*- coding: utf-8 -*-

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)

# WhatsApp state → our frontend enum
_WA_STATE_MAP = {
    'outgoing': 'pending',
    'sent': 'sent',
    'delivered': 'sent',
    'read': 'sent',
    'cancel': 'failed',
    'error': 'failed',
    'failed': 'failed',
}


class WhatsAppMessage(models.Model):
    """Override whatsapp.message to push bus.bus notifications whenever
    the delivery state changes (outgoing → sent → delivered → read / error).

    This lets the frontend update send_status / is_read in real-time via
    the existing long-poll connection instead of polling.
    """

    _inherit = 'whatsapp.message'

    def write(self, vals):
        state_changed = 'state' in vals
        old_states = {rec.id: rec.state for rec in self} if state_changed else {}

        result = super().write(vals)

        if state_changed:
            new_state = vals['state']
            for rec in self:
                if old_states.get(rec.id) == new_state:
                    continue
                self._push_status_update(rec, new_state)

        return result

    @api.model
    def _push_status_update(self, wa_msg, new_state):
        """Find the dke.chat.room linked to this WA message and push a
        chat.status_update bus event so the frontend can patch the bubble."""
        try:
            if not wa_msg.mail_message_id:
                return

            mail_msg = wa_msg.mail_message_id
            # The mail.message must be on a discuss.channel
            if mail_msg.model != 'discuss.channel' or not mail_msg.res_id:
                return

            room = self.env['dke.chat.room'].sudo().search(
                [('discuss_channel_id', '=', mail_msg.res_id)], limit=1
            )
            if not room:
                return

            send_status = _WA_STATE_MAP.get(new_state, 'sent')
            is_read = new_state == 'read'

            self.env['bus.bus']._sendone(
                'dke_chat_room_%s' % room.id,
                'chat.status_update',
                {
                    'room_id': room.id,
                    'message_id': mail_msg.id,
                    'send_status': send_status,
                    'is_read': is_read,
                },
            )
        except Exception:
            _logger.debug(
                'Failed to push status_update for whatsapp.message %s', wa_msg.id,
                exc_info=True,
            )
