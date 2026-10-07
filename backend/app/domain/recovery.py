from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def recovery_timestamp() -> datetime:
    return datetime.now(UTC)


class RecoveryCredential(Base):
    """One high-entropy, reusable account credential; plaintext is never retained."""

    __tablename__ = "recovery_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=recovery_timestamp)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=recovery_timestamp)
