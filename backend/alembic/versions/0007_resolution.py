"""detections.resolution (Investigations workflow)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15 10:00:00.000000

Nullable, so existing rows need no back-fill. The resolution note lives in the detection event log and the audit log.
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('detections') as batch:
        batch.add_column(sa.Column('resolution', sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('detections') as batch:
        batch.drop_column('resolution')
