"""Retain each user's immutable conversation creation request key."""

from alembic import op
import sqlalchemy as sa

revision = "0007_conversation_key"
down_revision = "0006_relation"
branch_labels = None
depends_on = None


def upgrade():
    # NULL keys preserve existing rows and clients which do not send a key.
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("client_conversation_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("initial_preferences_hash", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_conversation_client_request", ["user_id", "client_conversation_id"]
        )


def downgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("uq_conversation_client_request", type_="unique")
        batch.drop_column("initial_preferences_hash")
        batch.drop_column("client_conversation_id")
