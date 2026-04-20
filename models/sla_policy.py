# -*- coding: utf-8 -*-

from odoo import models, fields, api


class SLAPolicy(models.Model):
    """SLA Policy for tracking response/resolution time targets.

    Defines acceptable timeframes for staff actions, per priority level.
    Used to compute SLA compliance rates in performance evaluation.
    """
    _name = 'dke.sla.policy'
    _description = 'SLA Policy'
    _order = 'target_type, priority desc'

    name = fields.Char(string='Policy Name', required=True)
    target_type = fields.Selection([
        ('first_response', 'First Response'),
        ('resolution', 'Resolution'),
        ('chat_response', 'Chat Response'),
    ], string='Target Type', required=True)
    priority = fields.Selection([
        ('0', 'Low'),
        ('1', 'Normal'),
        ('2', 'High'),
        ('3', 'Urgent'),
    ], string='Priority', help='Ticket priority this SLA applies to. Leave empty for chat SLA.')
    max_minutes = fields.Integer(
        string='Max Time (minutes)', required=True,
        help='Maximum acceptable time in minutes for this SLA target.'
    )
    applies_to = fields.Selection([
        ('expert_staff', 'Expert Staff'),
        ('customer_care', 'Customer Care'),
        ('both', 'Both'),
    ], string='Applies To', required=True, default='expert_staff')
    active = fields.Boolean(string='Active', default=True)

    @api.model
    def get_sla_minutes(self, target_type, priority=None, role='expert_staff'):
        """Get the SLA max_minutes for a given target type and priority.

        Returns max_minutes or None if no matching policy found.
        """
        domain = [
            ('target_type', '=', target_type),
            ('active', '=', True),
            ('applies_to', 'in', [role, 'both']),
        ]
        if priority is not None:
            domain.append(('priority', '=', str(priority)))
        policy = self.search(domain, limit=1)
        return policy.max_minutes if policy else None
