"""Preserve document tombstones to prevent stale infrastructure resurrection."""
from alembic import op
import sqlalchemy as sa

revision = "0004_documents"
down_revision = "0003_context"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("context_documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.String(80), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False))


def downgrade():
    op.drop_table("context_documents")
