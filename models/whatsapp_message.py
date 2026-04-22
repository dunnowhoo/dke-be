# -*- coding: utf-8 -*-

import json
import logging
import threading

import requests as _requests

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

            # Mirror status update to FE webhook -> Redis SSE pipeline
            # so Vercel clients also receive near real-time read/send updates.
            self._push_status_webhook(room.id, mail_msg.id, send_status, is_read)
        except Exception:
            _logger.debug(
                'Failed to push status_update for whatsapp.message %s', wa_msg.id,
                exc_info=True,
            )

    @api.model
    def _push_status_webhook(self, room_id, message_id, send_status, is_read):
        """Push a chat.status_update event to FE webhook (if configured)."""
        ICP = self.env['ir.config_parameter'].sudo()
        webhook_url = ICP.get_param('dke.chat.webhook_url', '')
        if not webhook_url:
            return

        webhook_secret = ICP.get_param('dke.chat.webhook_secret', '')
        headers = {'Content-Type': 'application/json'}
        if webhook_secret:
            headers['x-webhook-secret'] = webhook_secret

        payload = {
            'room_id': room_id,
            'event': 'chat.status_update',
            'message': {
                'id': message_id,
                'send_status': send_status,
                'is_read': is_read,
            },
        }

        def _post():
            try:
                _requests.post(
                    webhook_url,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=5,
                )
            except Exception:
                _logger.debug(
                    'FE status webhook call failed for room %s', room_id,
                    exc_info=True,
                )

        threading.Thread(target=_post, daemon=True).start()
