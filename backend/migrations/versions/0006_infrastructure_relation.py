"""Separate the document subject from facility containment."""
from alembic import op
import sqlalchemy as sa

revision = "0006_relation"
down_revision = "0005_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project_facts", sa.Column("relation", sa.String(30), nullable=False, server_default="unspecified"))


def downgrade():
    op.drop_column("project_facts", "relation")
