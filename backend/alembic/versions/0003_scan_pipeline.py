"""scan pipeline: scan reports, detection history, official match keys, report drafts, look-alike cases

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('scans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('started_by', sa.String(length=120), nullable=False, server_default='scheduler'))
        batch_op.add_column(sa.Column('counts', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('source_report', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('timings', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('errors', sa.JSON(), nullable=False, server_default='[]'))
    # old state values (done / error) -> new status values
    op.execute("UPDATE scans SET state = 'completed' WHERE state = 'done'")
    op.execute("UPDATE scans SET state = 'failed' WHERE state = 'error'")

    with op.batch_alter_table('detections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('canonical_key', sa.String(length=400), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_scan_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('score_history', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('notes', sa.JSON(), nullable=False, server_default='[]'))
    op.execute("UPDATE detections SET canonical_key = 'legacy:' || id WHERE canonical_key = ''")
    with op.batch_alter_table('detections', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_detections_last_scan_id_scans'), 'scans', ['last_scan_id'], ['id'], ondelete='SET NULL')
        batch_op.create_unique_constraint(batch_op.f('uq_detections_brand_id'), ['brand_id', 'canonical_key'])

    op.create_table('detection_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('detection_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.String(length=120), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['detection_id'], ['detections.id'], name=op.f('fk_detection_events_detection_id_detections'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_detection_events'))
    )
    with op.batch_alter_table('detection_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_detection_events_detection_id'), ['detection_id'], unique=False)

    with op.batch_alter_table('official_matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('matched_rule', sa.String(length=300), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('match_key', sa.String(length=400), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('times_seen', sa.Integer(), nullable=False, server_default='1'))
    op.execute("UPDATE official_matches SET match_key = 'legacy:' || id WHERE match_key = ''")
    with op.batch_alter_table('official_matches', schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f('uq_official_matches_brand_id'), ['brand_id', 'match_key'])

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=200), nullable=False, server_default='Brand Protection Report'))
        batch_op.add_column(sa.Column('status', sa.String(length=10), nullable=False, server_default='draft'))
        batch_op.add_column(sa.Column('detection_ids', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('snapshot', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('approved_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_reports_approved_by_users'), 'users', ['approved_by'], ['id'], ondelete='SET NULL')

    op.create_table('lookalike_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('expected', sa.String(length=15), nullable=False),
    sa.Column('builtin', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_lookalike_cases_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lookalike_cases'))
    )


def downgrade() -> None:
    op.drop_table('lookalike_cases')
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_reports_approved_by_users'), type_='foreignkey')
        for col in ('approved_at', 'approved_by', 'snapshot', 'detection_ids', 'status', 'title'):
            batch_op.drop_column(col)
    with op.batch_alter_table('official_matches', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_official_matches_brand_id'), type_='unique')
        for col in ('times_seen', 'last_seen_at', 'url', 'match_key', 'matched_rule'):
            batch_op.drop_column(col)
    with op.batch_alter_table('detection_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_detection_events_detection_id'))
    op.drop_table('detection_events')
    with op.batch_alter_table('detections', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_detections_brand_id'), type_='unique')
        batch_op.drop_constraint(batch_op.f('fk_detections_last_scan_id_scans'), type_='foreignkey')
        for col in ('notes', 'score_history', 'last_scan_id', 'last_seen_at', 'first_seen_at', 'canonical_key'):
            batch_op.drop_column(col)
    op.execute("UPDATE scans SET state = 'done' WHERE state IN ('completed', 'partial')")
    op.execute("UPDATE scans SET state = 'error' WHERE state = 'failed'")
    with op.batch_alter_table('scans', schema=None) as batch_op:
        for col in ('errors', 'timings', 'source_report', 'counts', 'started_by'):
            batch_op.drop_column(col)
