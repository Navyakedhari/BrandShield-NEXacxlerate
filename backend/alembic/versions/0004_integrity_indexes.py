"""partial unique indexes: one active scan per brand, built-in look-alike cases seeded once

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13 20:00:00.000000

Works on SQLite and PostgreSQL (both support partial indexes). Existing duplicates are cleaned up first so the
index can be created on a database that already ran into the race.
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

ACTIVE = "state IN ('queued', 'running')"


def upgrade() -> None:
    # More than one active scan for a brand (only possible after a race): keep the newest, fail the others.
    op.execute(
        "UPDATE scans SET state = 'failed', message = 'Superseded by a newer scan (duplicate removed by migration 0004).' "
        f"WHERE {ACTIVE} AND id NOT IN (SELECT MAX(id) FROM scans WHERE {ACTIVE} GROUP BY brand_id)"
    )
    # Built-in look-alike cases seeded twice: keep the oldest row of each name.
    op.execute(
        sa.text(
            "DELETE FROM lookalike_cases WHERE builtin = :t AND id NOT IN "
            "(SELECT MIN(id) FROM lookalike_cases WHERE builtin = :t GROUP BY name)"
        ).bindparams(sa.bindparam('t', True, type_=sa.Boolean()))
    )
    op.create_index(
        'uq_scans_one_active_per_brand', 'scans', ['brand_id'], unique=True,
        sqlite_where=sa.text(ACTIVE), postgresql_where=sa.text(ACTIVE),
    )
    op.create_index(
        'uq_lookalike_cases_builtin_name', 'lookalike_cases', ['name'], unique=True,
        sqlite_where=sa.text('builtin'), postgresql_where=sa.text('builtin'),
    )


def downgrade() -> None:
    op.drop_index('uq_lookalike_cases_builtin_name', table_name='lookalike_cases')
    op.drop_index('uq_scans_one_active_per_brand', table_name='scans')
