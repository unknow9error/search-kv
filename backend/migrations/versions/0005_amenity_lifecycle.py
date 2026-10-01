"""Unknown/planned point data must not satisfy operating infrastructure filters."""
from alembic import op
import sqlalchemy as sa

revision = "0005_lifecycle"
down_revision = "0004_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("amenities", sa.Column("state", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("places", sa.Column("state", sa.String(30), nullable=False, server_default="unknown"))


def downgrade():
    op.drop_column("places", "state")
    op.drop_column("amenities", "state")
