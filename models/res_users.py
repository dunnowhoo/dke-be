# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResUsers(models.Model):
    """Extend res.users with DKE CRM role fields and expert performance metrics."""
    _inherit = 'res.users'

    dke_role = fields.Selection([
        ('customer_care', 'Customer Care'),
        ('sales_staff', 'Sales Staff'),
        ('sales_manager', 'Sales Manager'),
        ('expert_staff', 'Expert Staff'),
    ], string='DKE Role')

    dke_status = fields.Selection([
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ], string='DKE Status', default='active')

    dke_specialization = fields.Selection([
        ('teknis', 'Teknis'),
        ('produk', 'Produk'),
        ('pengiriman', 'Pengiriman'),
    ], string='Specialization')

    dke_phone = fields.Char(string='DKE Phone')

    # ------------------------------------------------------------------ #
    # Performance stats — stored, refreshed by _recompute_expert_stats()  #
    # ------------------------------------------------------------------ #
    avg_response_time = fields.Float(string='Avg Response Time (min)')
    avg_resolution_time = fields.Float(
        string='Avg Resolution Time (hours)',
        help='Average hours from ticket creation to resolution for this expert.',
    )
    avg_rating = fields.Float(string='Avg Customer Rating')
    total_chats_handled = fields.Integer(string='Total Chats Handled')
    total_tickets_resolved = fields.Integer(
        string='Total Tickets Resolved',
        help='Number of tickets with state resolved/closed assigned to this expert.',
    )
    total_messages_sent = fields.Integer(string='Total Messages Sent')
    avg_resolution_message_count = fields.Float(
        string='Avg Messages per Resolution',
        help='Average number of chat messages from ticket open to close.',
    )

    # Expert-specific: CS→Expert rating stats
    expert_avg_cs_rating = fields.Float(
        string='Avg CS Rating (Expert)',
        help='Average rating given by Customer Care for this expert.')
    expert_sla_compliance_rate = fields.Float(
        string='Expert SLA Compliance (%)',
        help='Percentage of tickets where expert responded within SLA.')
    expert_avg_response_minutes = fields.Float(
        string='Expert Avg Response (min)',
        help='Average minutes expert takes to respond to CS in ticketing.')

    # Customer Care-specific: Customer→CS rating stats
    care_avg_rating = fields.Float(
        string='Avg Customer Rating (Care)',
        help='Average rating given by customers for this CS agent.')
    care_avg_response_minutes = fields.Float(
        string='Care Avg Response (min)',
        help='Average minutes CS takes to respond to customer messages.')
    care_total_chats_rated = fields.Integer(
        string='Total Chats Rated',
        help='Number of chats where customer provided a rating.')
    care_sla_compliance_rate = fields.Float(
        string='Care SLA Compliance (%)',
        help='Percentage of chats where CS responded within SLA.')

    # ------------------------------------------------------------------ #
    # Recompute helper — called after ticket resolve/close                 #
    # ------------------------------------------------------------------ #

    def _recompute_expert_stats(self):
        """Recompute stored performance stats for this expert user."""
        Ticket = self.env['helpdesk.ticket'].sudo()
        Session = self.env['dke.ticketing.session'].sudo()
        SLA = self.env['dke.sla.policy'].sudo()
        closed_stage_ids = self.env['helpdesk.stage'].sudo().search([('fold', '=', True)]).ids

        for user in self:
            resolved_tickets = Ticket.search([
                ('user_id', '=', user.id),
                ('stage_id', 'in', closed_stage_ids),
            ])

            count = len(resolved_tickets)
            if count:
                avg_hours = sum(t.close_hours or 0 for t in resolved_tickets) / count

                # CS→Expert ratings from sessions
                cs_ratings = []
                response_times = []
                total_msgs = 0
                sla_met = 0

                for t in resolved_tickets:
                    if t.channel_id:
                        total_msgs += self.env['dke.ticketing.message'].sudo().search_count([
                            ('room_id', '=', t.channel_id.id),
                        ])
                        for s in t.channel_id.session_ids:
                            if s.cs_expert_rating:
                                cs_ratings.append(int(s.cs_expert_rating))
                            if s.expert_avg_response_minutes:
                                response_times.append(s.expert_avg_response_minutes)

                    # SLA compliance: check first response time
                    if t.expert_assigned_at and t.expert_first_response_at:
                        response_min = (t.expert_first_response_at - t.expert_assigned_at).total_seconds() / 60.0
                        sla_limit = SLA.get_sla_minutes('first_response', t.priority, 'expert_staff')
                        if sla_limit and response_min <= sla_limit:
                            sla_met += 1
                        elif not sla_limit:
                            sla_met += 1  # No SLA defined = compliant

                avg_cs_rating = sum(cs_ratings) / len(cs_ratings) if cs_ratings else 0.0
                avg_response = sum(response_times) / len(response_times) if response_times else 0.0
                avg_msgs = total_msgs / count
                sla_rate = (sla_met / count) * 100 if count else 0.0
            else:
                avg_hours = 0.0
                avg_cs_rating = 0.0
                avg_response = 0.0
                avg_msgs = 0.0
                sla_rate = 0.0

            # Also compute customer_rating avg (from session.customer_rating)
            customer_ratings = []
            for t in resolved_tickets:
                if t.channel_id:
                    for s in t.channel_id.session_ids:
                        if s.customer_rating:
                            customer_ratings.append(int(s.customer_rating))
            avg_cust_rating = sum(customer_ratings) / len(customer_ratings) if customer_ratings else 0.0

            user.write({
                'total_tickets_resolved': count,
                'avg_resolution_time': round(avg_hours, 2),
                'avg_resolution_message_count': round(avg_msgs, 2),
                'avg_rating': round(avg_cust_rating, 2),
                'expert_avg_cs_rating': round(avg_cs_rating, 2),
                'expert_avg_response_minutes': round(avg_response, 2),
                'expert_sla_compliance_rate': round(sla_rate, 2),
            })

    def _recompute_care_stats(self):
        """Recompute stored performance stats for Customer Care user."""
        ChatRoom = self.env['dke.chat.room'].sudo()
        SLA = self.env['dke.sla.policy'].sudo()

        for user in self:
            # Rated chat rooms assigned to this CS
            rated_rooms = ChatRoom.search([
                ('assigned_to', '=', user.id),
                ('is_rated', '=', True),
            ])

            all_rooms = ChatRoom.search([
                ('assigned_to', '=', user.id),
                ('state', 'in', ['done', 'archived']),
            ])

            # Ratings
            ratings = []
            for room in rated_rooms:
                if room.customer_care_rating:
                    ratings.append(int(room.customer_care_rating))

            avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

            # Response time: avg time CS takes to reply to customer messages
            Message = self.env['dke.chat.message'].sudo()
            all_response_times = []
            sla_met = 0
            total_checked = 0
            sla_limit = SLA.get_sla_minutes('chat_response', role='customer_care')

            for room in all_rooms:
                messages = Message.search([
                    ('room_id', '=', room.id),
                ], order='created_at asc')

                last_customer_time = None
                for msg in messages:
                    if msg.sender_type == 'customer':
                        last_customer_time = msg.created_at
                    elif msg.sender_type == 'admin' and msg.sender_id and msg.sender_id.id == user.id:
                        if last_customer_time and msg.created_at:
                            diff_min = (msg.created_at - last_customer_time).total_seconds() / 60.0
                            if diff_min > 0:
                                all_response_times.append(diff_min)
                                total_checked += 1
                                if sla_limit and diff_min <= sla_limit:
                                    sla_met += 1
                                elif not sla_limit:
                                    sla_met += 1
                        last_customer_time = None

            avg_response = sum(all_response_times) / len(all_response_times) if all_response_times else 0.0
            sla_rate = (sla_met / total_checked) * 100 if total_checked else 0.0

            user.write({
                'care_avg_rating': round(avg_rating, 2),
                'care_avg_response_minutes': round(avg_response, 2),
                'care_total_chats_rated': len(ratings),
                'care_sla_compliance_rate': round(sla_rate, 2),
                'total_chats_handled': len(all_rooms),
            })
