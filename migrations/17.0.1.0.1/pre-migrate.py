# -*- coding: utf-8 -*-
"""Pre-migration: remove orphaned ir.model records for dke.support.ticket.

The model dke.support.ticket (previously in models/support_ticket.py) was
removed from the codebase. Odoo's _process_end tries to clean up leftover
ir.model.fields.selection records but crashes because the model is no
longer in the registry (KeyError: 'dke.support.ticket').

This script purges all ORM metadata for that model BEFORE the registry
is built, preventing the crash.
"""

import logging

_logger = logging.getLogger(__name__)

MODEL_NAME = 'dke.support.ticket'


def migrate(cr, version):
    _logger.info("pre-migrate: cleaning up orphaned records for %s", MODEL_NAME)

    # 1. Remove selection options on fields belonging to the deleted model
    cr.execute("""
        DELETE FROM ir_model_fields_selection
        WHERE field_id IN (
            SELECT id FROM ir_model_fields
            WHERE model_id IN (
                SELECT id FROM ir_model WHERE model = %s
            )
        )
    """, (MODEL_NAME,))
    _logger.info("pre-migrate: deleted %d ir_model_fields_selection rows", cr.rowcount)

    # 2. Remove constraints associated with the deleted model
    cr.execute("""
        DELETE FROM ir_model_constraint
        WHERE model_id IN (
            SELECT id FROM ir_model WHERE model = %s
        )
    """, (MODEL_NAME,))
    _logger.info("pre-migrate: deleted %d ir_model_constraint rows", cr.rowcount)

    # 3. Remove many2many relation metadata
    cr.execute("""
        DELETE FROM ir_model_relation
        WHERE model_id IN (SELECT id FROM ir_model WHERE model = %s)
           OR subject_model_id IN (SELECT id FROM ir_model WHERE model = %s)
    """, (MODEL_NAME, MODEL_NAME))
    _logger.info("pre-migrate: deleted %d ir_model_relation rows", cr.rowcount)

    # 4. Remove fields belonging to the deleted model
    cr.execute("""
        DELETE FROM ir_model_fields
        WHERE model_id IN (
            SELECT id FROM ir_model WHERE model = %s
        )
    """, (MODEL_NAME,))
    _logger.info("pre-migrate: deleted %d ir_model_fields rows", cr.rowcount)

    # 5. Remove ir.model.data entries pointing at this model's ir.model record
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE model = 'ir.model'
          AND res_id IN (SELECT id FROM ir_model WHERE model = %s)
    """, (MODEL_NAME,))
    _logger.info("pre-migrate: deleted %d ir_model_data rows", cr.rowcount)

    # 6. Remove the ir.model record itself
    cr.execute("DELETE FROM ir_model WHERE model = %s", (MODEL_NAME,))
    _logger.info("pre-migrate: deleted %d ir_model rows", cr.rowcount)

    _logger.info("pre-migrate: cleanup for %s complete", MODEL_NAME)
