# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
import json


class MarketingController(http.Controller):
    """REST API endpoints for Marketing campaigns.

    EPIC03 - PBI-6, PBI-7, PBI-8
    """

    @http.route('/api/marketing/campaigns', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def create_campaign(self, **kwargs):
        """POST /api/marketing/campaigns — Create campaign draft.

        PBI-6: Validates image, discount, and creates draft campaign.
        Request Body: Multipart (title, description, product_id, discount_type, discount_value, image_file)
        """
        # TODO: Implement campaign creation with image upload
        return request.make_json_response({'status': 'ok', 'campaign_id': None})

    @http.route('/api/marketing/segmentation/preview', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def preview_segmentation(self, **kwargs):
        """POST /api/marketing/segmentation/preview — Preview customer segment.

        PBI-7: Translates filter rules to SQL, returns matched count and sample.
        Request Body: { "rules": [{ "field": "...", "operator": "...", "value": ... }] }
        """
        # TODO: Implement dynamic segmentation query
        return request.make_json_response({'status': 'ok', 'matched_count': 0, 'sample': []})

    @http.route('/api/marketing/campaigns/<int:campaign_id>/target', type='http', auth='user', methods=['PUT'], csrf=False, cors='*')
    def save_target(self, campaign_id, **kwargs):
        """PUT /api/marketing/campaigns/{id}/target — Save segmentation rules.

        PBI-7: Saves rules to campaign.
        """
        # TODO: Implement target saving
        return request.make_json_response({'status': 'ok'})

    @http.route('/api/marketing/campaigns/<int:campaign_id>/send', type='http', auth='user', methods=['POST'], csrf=False, cors='*')
    def send_campaign(self, campaign_id, **kwargs):
        """POST /api/marketing/campaigns/{id}/send — Trigger broadcast.

        PBI-8: Queues campaign for async sending with throttling.
        """
        # TODO: Implement async campaign broadcast
        return request.make_json_response({'status': 'ok'})

    @http.route('/api/marketing/campaigns/<int:campaign_id>/status', type='http', auth='user', methods=['GET'], csrf=False, cors='*')
    def get_campaign_status(self, campaign_id, **kwargs):
        """GET /api/marketing/campaigns/{id}/status — Get broadcast progress.

        PBI-8: Returns sent_count, failed_count, and overall status.
        """
        # TODO: Implement status polling
        return request.make_json_response({'status': 'ok', 'sent_count': 0, 'failed_count': 0})
