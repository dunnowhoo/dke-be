# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase


class TestMarketingCampaign(TransactionCase):
    """Test cases for dke.marketing.campaign model.

    EPIC03 - PBI-6, PBI-7, PBI-8
    """

    def setUp(self):
        super().setUp()
        self.Campaign = self.env['dke.marketing.campaign']

    def test_create_campaign_draft(self):
        """Test creating a campaign in draft state."""
        campaign = self.Campaign.create({
            'name': 'Test Campaign',
            'description': 'Test campaign description',
        })
        self.assertEqual(campaign.state, 'draft')

    def test_campaign_state_workflow(self):
        """Test campaign state workflow: draft → targeted → processing → done."""
        # TODO: Implement state workflow tests
        pass

    def test_campaign_broadcast_counts(self):
        """Test broadcast sent/failed count tracking."""
        # TODO: Implement broadcast count tests
        pass
