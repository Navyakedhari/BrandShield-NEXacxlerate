"""registry v2 (brand description, soft delete, extra logos, publisher ids, verification), candidate origin and images, text-analysis cache

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14 16:00:00.000000

* brands.description (text) and brands.archived_at (soft delete: a brand is never erased).
* brand_logos: additional official logos beside the first one kept on brands.logo_*.
* brand_publishers: official publisher / developer identifiers per store.
* asset_verifications: unverified / verified (method, when, by whom) per official identifier.
* detections.origin: scan | manual | import | synthetic. Existing sample rows are back-filled as synthetic, the rest as scan.
* candidate_images: re-encoded uploads for manual / imported candidates and app icons, with their perceptual hashes.
* text_analysis_cache: persistent cache of validated hosted-model answers.
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('brands') as batch:
        batch.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch.add_column(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('detections') as batch:
        batch.add_column(sa.Column('origin', sa.String(12), nullable=False, server_default='scan'))
    op.execute("UPDATE detections SET origin = 'synthetic' WHERE source = 'sample'")

    op.create_table('brand_logos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brand_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('format', sa.String(length=10), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_brand_logos_brand_id_brands'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_brand_logos')),
        sa.UniqueConstraint('brand_id', 'sha256', name=op.f('uq_brand_logos_brand_id')),
    )
    with op.batch_alter_table('brand_logos') as batch:
        batch.create_index(batch.f('ix_brand_logos_brand_id'), ['brand_id'], unique=False)

    op.create_table('brand_publishers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brand_id', sa.Integer(), nullable=False),
        sa.Column('store', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('publisher_id', sa.String(length=120), nullable=True),
        sa.Column('publisher_key', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_brand_publishers_brand_id_brands'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_brand_publishers')),
        sa.UniqueConstraint('brand_id', 'store', 'publisher_key', name=op.f('uq_brand_publishers_brand_id')),
    )
    with op.batch_alter_table('brand_publishers') as batch:
        batch.create_index(batch.f('ix_brand_publishers_brand_id'), ['brand_id'], unique=False)

    op.create_table('asset_verifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brand_id', sa.Integer(), nullable=False),
        sa.Column('asset_type', sa.String(length=12), nullable=False),
        sa.Column('asset_key', sa.String(length=300), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('method', sa.String(length=40), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_by', sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_asset_verifications_brand_id_brands'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_verifications')),
        sa.UniqueConstraint('brand_id', 'asset_type', 'asset_key', name=op.f('uq_asset_verifications_brand_id')),
    )
    with op.batch_alter_table('asset_verifications') as batch:
        batch.create_index(batch.f('ix_asset_verifications_brand_id'), ['brand_id'], unique=False)

    op.create_table('candidate_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brand_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('format', sa.String(length=10), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('hashes', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_candidate_images_brand_id_brands'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_images')),
        sa.UniqueConstraint('brand_id', 'sha256', name=op.f('uq_candidate_images_brand_id')),
    )
    with op.batch_alter_table('candidate_images') as batch:
        batch.create_index(batch.f('ix_candidate_images_brand_id'), ['brand_id'], unique=False)

    op.create_table('text_analysis_cache',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_text_analysis_cache')),
    )


def downgrade() -> None:
    op.drop_table('text_analysis_cache')
    op.drop_table('candidate_images')
    op.drop_table('asset_verifications')
    op.drop_table('brand_publishers')
    op.drop_table('brand_logos')
    with op.batch_alter_table('detections') as batch:
        batch.drop_column('origin')
    with op.batch_alter_table('brands') as batch:
        batch.drop_column('archived_at')
        batch.drop_column('description')
