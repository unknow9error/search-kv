"""Independent public project observations and user-owned aggregator state."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class ProjectRecord(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_project_provider_external"),
        Index("ix_project_catalog_city", "city", "district"),
        Index("ix_project_catalog_observed", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str | None] = mapped_column(String(300))
    developer_name: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    record_origin: Mapped[str] = mapped_column(String(30), default="public_project")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectLayoutRecord(Base):
    __tablename__ = "project_layouts"
    __table_args__ = (UniqueConstraint("project_id", "external_id", name="uq_project_layout_external"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    rooms: Mapped[int | None] = mapped_column(Integer)
    area_m2: Mapped[float | None] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(String(2000))
    source_url: Mapped[str] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectFavorite(Base):
    __tablename__ = "project_favorites"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectConversationRecord(Base):
    __tablename__ = "project_conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "client_conversation_id", name="uq_project_conversation_client"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_conversation_id: Mapped[str | None] = mapped_column(String(36))
    initial_criteria_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(100), default="Поиск ЖК")
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProjectTurnRecord(Base):
    __tablename__ = "project_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_turn_id", name="uq_project_turn_client"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("project_conversations.id", ondelete="CASCADE"), index=True
    )
    client_turn_id: Mapped[str] = mapped_column(String(36))
    message: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="complete")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DataReportRecord(Base):
    __tablename__ = "data_reports"
    __table_args__ = (UniqueConstraint("user_id", "client_report_id", name="uq_data_report_client"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    client_report_id: Mapped[str] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
