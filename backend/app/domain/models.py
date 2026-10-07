from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Apartment(Base):
    __tablename__ = "apartments"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id"),
        Index("ix_catalog_filter", "city", "status", "rooms", "price_kzt"),
        Index("ix_catalog_freshness", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    complex_name: Mapped[str] = mapped_column(String(200))
    complex_id: Mapped[str | None] = mapped_column(String(120), index=True)
    bigville_id: Mapped[str | None] = mapped_column(String(120), index=True)
    bigville_name: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(300))
    rooms: Mapped[int] = mapped_column(Integer)
    area_m2: Mapped[float] = mapped_column(Float)
    floor: Mapped[int] = mapped_column(Integer)
    total_floors: Mapped[int] = mapped_column(Integer)
    price_kzt: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    finish: Mapped[str] = mapped_column(String(120), default="Не указана")
    completion: Mapped[str] = mapped_column(String(100), default="Уточняется")
    source_url: Mapped[str] = mapped_column(String(2000))
    image_url: Mapped[str | None] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Amenity(Base):
    __tablename__ = "amenities"
    __table_args__ = (Index("ix_amenity_search", "apartment_id", "kind", "distance_m"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    apartment_id: Mapped[str] = mapped_column(ForeignKey("apartments.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="unknown")
    name: Mapped[str] = mapped_column(String(200))
    distance_m: Mapped[int] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (Index("ix_places_city_coordinates", "city", "latitude", "longitude"),)
    id: Mapped[str] = mapped_column(String(140), primary_key=True)
    city: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="unknown")
    name: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectFact(Base):
    __tablename__ = "project_facts"
    __table_args__ = (Index("ix_project_facts_scope", "provider_id", "scope_type", "scope_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(30))
    relation: Mapped[str] = mapped_column(String(30), default="unspecified")
    expected_opening: Mapped[str | None] = mapped_column(String(100))
    evidence: Mapped[str] = mapped_column(String(1000))
    source_url: Mapped[str] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextDocument(Base):
    __tablename__ = "context_documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "client_conversation_id", name="uq_conversation_client_request"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_conversation_id: Mapped[str | None] = mapped_column(String(36))
    initial_preferences_hash: Mapped[str | None] = mapped_column(String(64))
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    last_listing_ids: Mapped[list] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(100), default="Найти свой дом")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("conversation_id", "client_turn_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    client_turn_id: Mapped[str] = mapped_column(String(36))
    message: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TurnEvent(Base):
    __tablename__ = "turn_events"
    __table_args__ = (UniqueConstraint("turn_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)


class Favorite(Base):
    __tablename__ = "favorites"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    apartment_id: Mapped[str] = mapped_column(
        ForeignKey("apartments.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2000))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


# Register the separate recovery model for schema creation and Alembic discovery.
from app.domain import project_models  # noqa: E402, F401
from app.domain.recovery import RecoveryCredential  # noqa: E402, F401
