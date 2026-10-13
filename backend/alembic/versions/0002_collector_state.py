"""collector state (health, back-off, result cache)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('collector_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collector', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=15), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=True),
    sa.Column('message', sa.String(length=400), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cache_brand_id', sa.Integer(), nullable=True),
    sa.Column('cache', sa.JSON(), nullable=True),
    sa.Column('cached_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_collector_state')),
    sa.UniqueConstraint('collector', name=op.f('uq_collector_state_collector'))
    )


def downgrade() -> None:
    op.drop_table('collector_state')
