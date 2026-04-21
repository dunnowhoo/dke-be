# -*- coding: utf-8 -*-

import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# How long (seconds) to hold the connection open waiting for notifications
_POLL_TIMEOUT = 45
# Interval (seconds) between DB checks while waiting
_POLL_INTERVAL = 1


class BusController(http.Controller):
    """Custom long-poll endpoint for dke_crm bus notifications.

    Uses the caller's Odoo session (auth='user'), so it works with the same
    session cookie / Bearer→SID mechanism the rest of our API uses.

    POST /api/chat/bus/poll
    Body : { "channels": ["dke_chat_room_1", ...], "last": 0 }
    Returns: Array of { id, channel, message: { type, payload } }
    """

    @http.route(
        '/api/chat/bus/poll',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
        cors='*',
    )
    def bus_poll(self, **_kwargs):
        try:
            body = json.loads(request.httprequest.data or b'{}')
        except (ValueError, TypeError):
            body = {}

        channels = body.get('channels', [])
        last = int(body.get('last', 0) or 0)

        if not isinstance(channels, list) or not channels:
            return request.make_response(
                json.dumps([]),
                headers=[('Content-Type', 'application/json')],
            )

        notifications = self._wait_for_notifications(channels, last)
        return request.make_response(
            json.dumps(notifications),
            headers=[('Content-Type', 'application/json')],
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _wait_for_notifications(self, channels, last):
        """Block (up to _POLL_TIMEOUT seconds) until ≥1 notification arrives
        for the given channels, then return all pending notifications.

        Falls back to an immediate empty list on any error.
        """
        deadline = time.time() + _POLL_TIMEOUT
        while True:
            try:
                result = self._fetch_notifications(channels, last)
            except Exception:
                _logger.debug('[BusPoll] DB fetch error', exc_info=True)
                result = []

            if result:
                return result

            remaining = deadline - time.time()
            if remaining <= 0:
                return []

            time.sleep(min(_POLL_INTERVAL, remaining))

    def _fetch_notifications(self, channels, last):
        """Query bus.bus for notifications newer than *last* on *channels*."""
        db = request.env.cr.dbname
        # Normalise channels to the same format bus.bus uses internally
        # (Odoo stores them as JSON-encoded strings with db prefix)
        from odoo.addons.bus.models.bus import channel_with_db
        normalised = [
            json.dumps(channel_with_db(db, c), sort_keys=True)
            for c in channels
        ]

        domain = [('channel', 'in', normalised)]
        if last:
            domain.append(('id', '>', last))
        else:
            # On first poll return only recent notifications (last 60 s)
            from odoo import fields
            import datetime
            cutoff = fields.Datetime.to_string(
                datetime.datetime.utcnow() - datetime.timedelta(seconds=60)
            )
            domain.append(('create_date', '>', cutoff))

        rows = request.env['bus.bus'].sudo().search_read(
            domain,
            fields=['id', 'channel', 'message'],
            order='id asc',
        )
        result = []
        for row in rows:
            try:
                msg = json.loads(row['message'])
            except (ValueError, TypeError):
                msg = {}
            try:
                raw_channel = json.loads(row['channel'])
                # raw_channel may be [db, channel_name] or just the channel string
                if isinstance(raw_channel, list) and len(raw_channel) == 2:
                    channel_name = raw_channel[1]
                else:
                    channel_name = str(raw_channel)
            except (ValueError, TypeError):
                channel_name = row['channel']

            result.append({
                'id': row['id'],
                'channel': channel_name,
                'message': msg,
            })
        return result
