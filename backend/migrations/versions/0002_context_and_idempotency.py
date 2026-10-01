"""Bounded listing context and complete request idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0002_context"
down_revision = "f98f7016cc62"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("last_listing_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("turns", sa.Column("request_hash", sa.String(64), nullable=False, server_default=""))


def downgrade():
    op.drop_column("turns", "request_hash")
    op.drop_column("conversations", "last_listing_ids")
