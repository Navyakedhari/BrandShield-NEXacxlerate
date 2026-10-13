"""risk engine columns (severity, algorithm_version, scored_at) and the official App Store numeric id

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 10:00:00.000000

* detections.severity / algorithm_version / scored_at. Existing rows are back-filled: severity from the stored score with the
  default cutoffs (LOW < 40, MEDIUM < 70, HIGH < 85, CRITICAL 85+), algorithm_version "1.0" (their scores came from the
  original scorer), scored_at = last update. `category` and `type` are plain strings, so the new `web` / `lookalike_domain`
  values need no schema change.
* official_apps.store_id: optional numeric App Store id so a listing that only exposes the id is recognised as official.
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('detections') as batch:
        batch.add_column(sa.Column('severity', sa.String(10), nullable=False, server_default='LOW'))
        batch.add_column(sa.Column('algorithm_version', sa.String(10), nullable=True))
        batch.add_column(sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE detections SET severity = CASE WHEN score >= 85 THEN 'CRITICAL' WHEN score >= 70 THEN 'HIGH' "
        "WHEN score >= 40 THEN 'MEDIUM' ELSE 'LOW' END, algorithm_version = '1.0', scored_at = updated_at"
    )
    with op.batch_alter_table('official_apps') as batch:
        batch.add_column(sa.Column('store_id', sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('official_apps') as batch:
        batch.drop_column('store_id')
    with op.batch_alter_table('detections') as batch:
        batch.drop_column('scored_at')
        batch.drop_column('algorithm_version')
        batch.drop_column('severity')
