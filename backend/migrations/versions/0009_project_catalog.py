"""Add independent public project records and user-owned aggregator state."""

import sqlalchemy as sa
from alembic import op

revision = "0009_project_catalog"
down_revision = "0008_account_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_id", sa.String(80), nullable=False),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("district", sa.String(120), nullable=False),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("developer_name", sa.String(200), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_origin", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "external_id", name="uq_project_provider_external"),
    )
    op.create_index("ix_projects_provider_id", "projects", ["provider_id"])
    op.create_index("ix_project_catalog_city", "projects", ["city", "district"])
    op.create_index("ix_project_catalog_observed", "projects", ["observed_at"])
    op.create_table(
        "project_layouts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("rooms", sa.Integer(), nullable=True),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("image_url", sa.String(2000), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "external_id", name="uq_project_layout_external"),
    )
    op.create_index("ix_project_layouts_project_id", "project_layouts", ["project_id"])
    op.create_table(
        "project_favorites",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "project_id"),
    )
    op.create_table(
        "project_conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("client_conversation_id", sa.String(36), nullable=True),
        sa.Column("initial_criteria_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_conversation_id", name="uq_project_conversation_client"),
    )
    op.create_index("ix_project_conversations_user_id", "project_conversations", ["user_id"])
    op.create_index("ix_project_conversations_updated_at", "project_conversations", ["updated_at"])
    op.create_table(
        "project_turns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("client_turn_id", sa.String(36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["project_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "client_turn_id", name="uq_project_turn_client"),
    )
    op.create_index("ix_project_turns_conversation_id", "project_turns", ["conversation_id"])
    op.create_index("ix_project_turns_created_at", "project_turns", ["created_at"])
    op.create_table(
        "data_reports",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("client_report_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_report_id", name="uq_data_report_client"),
    )
    op.create_index("ix_data_reports_user_id", "data_reports", ["user_id"])
    op.create_index("ix_data_reports_project_id", "data_reports", ["project_id"])
    op.create_index("ix_data_reports_created_at", "data_reports", ["created_at"])


def downgrade():
    # Only the tables introduced by this revision are removed; legacy catalog and chats remain.
    op.drop_table("data_reports")
    op.drop_table("project_turns")
    op.drop_table("project_conversations")
    op.drop_table("project_favorites")
    op.drop_table("project_layouts")
    op.drop_table("projects")
