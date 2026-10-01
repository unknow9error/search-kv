"""Developer infrastructure at complex and bigville scope."""
from alembic import op
import sqlalchemy as sa

revision = "0003_context"
down_revision = "0002_context"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("apartments", sa.Column("complex_id", sa.String(120), nullable=True))
    op.add_column("apartments", sa.Column("bigville_id", sa.String(120), nullable=True))
    op.add_column("apartments", sa.Column("bigville_name", sa.String(200), nullable=True))
    op.create_index("ix_apartments_complex_id", "apartments", ["complex_id"])
    op.create_index("ix_apartments_bigville_id", "apartments", ["bigville_id"])
    op.create_table("project_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_id", sa.String(80), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False), sa.Column("scope_id", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(30), nullable=False), sa.Column("expected_opening", sa.String(100)),
        sa.Column("evidence", sa.String(1000), nullable=False), sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_project_facts_scope", "project_facts", ["provider_id", "scope_type", "scope_id", "kind"])


def downgrade():
    op.drop_table("project_facts")
    op.drop_index("ix_apartments_bigville_id", "apartments")
    op.drop_index("ix_apartments_complex_id", "apartments")
    op.drop_column("apartments", "bigville_name")
    op.drop_column("apartments", "bigville_id")
    op.drop_column("apartments", "complex_id")
