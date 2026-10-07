"""Store only a digest of the optional account recovery credential."""

import sqlalchemy as sa
from alembic import op

revision = "0008_account_recovery"
down_revision = "0007_conversation_key"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recovery_credentials",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("code_hash"),
    )


def downgrade():
    op.drop_table("recovery_credentials")
