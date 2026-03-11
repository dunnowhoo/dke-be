# -*- coding: utf-8 -*-
{
    'name': 'DKE Smart Sales Platform',
    'version': '17.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'AI-Powered Omnichannel Sales & Customer Engagement System',
    'description': """
DKE Smart Sales Platform
=========================
AI-Powered Omnichannel Sales & Customer Engagement System built on Odoo 17.

Key Features:
- Unified Chat System with Marketplace Integration (Shopee, WhatsApp)
- Automated Follow-Up & Scheduled Messaging
- Personalized Marketing Campaign with Customer Segmentation
- Marketplace Transaction Sync & Analytics
- Support Ticketing System (Customer Care ↔ Expert Staff)
- Chat & Ticket Monitoring with SLA Tracking
- EIS Dashboard for Sales Manager
- Platform Order Management & Invoicing
    """,
    'author': 'Propenheimer',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'sale',
        'contacts',
        'whatsapp',
    ],
    'data': [
        # Security
        'security/dke_crm_security.xml',
        'security/ir.model.access.csv',

        # Data
        'data/dke_crm_data.xml',
        'data/shopee_cron.xml',
        'data/dke_crm_demo_users.xml',

        # Views
        'views/chat_room_views.xml',
        'views/chat_message_views.xml',
        'views/ticketing_room_views.xml',
        'views/ticketing_message_views.xml',
        'views/marketing_campaign_views.xml',
        'views/customer_segment_views.xml',
        'views/sale_transaction_views.xml',
        'views/support_ticket_views.xml',
        'views/dashboard_views.xml',
        'views/shopee_integration_views.xml',
        'views/faq_article_views.xml',
        'views/menu_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': 10,
}
