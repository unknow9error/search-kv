import hashlib
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.db import Database
from app.domain.models import Conversation, User
from app.domain.schemas import ConversationRequest


def creation_hash(body: ConversationRequest) -> str:
    # Hash the validated defaults/normalization, never the conversation's mutable preferences.
    return hashlib.sha256(body.preferences.model_dump_json().encode()).hexdigest()


def check_creation(existing: Conversation, request_hash: str) -> Conversation:
    if existing.initial_preferences_hash != request_hash:
        raise HTTPException(409, "idempotency_conflict")
    return existing


async def create_conversation(
    database: Database,
    user_id: str,
    body: ConversationRequest,
    limit: Callable[[], Awaitable[None]],
) -> Conversation:
    client_id = str(body.client_conversation_id) if body.client_conversation_id else None
    request_hash = creation_hash(body)
    query = select(Conversation).where(
        Conversation.user_id == user_id, Conversation.client_conversation_id == client_id
    )
    try:
        async with database.sessions.begin() as db:
            # Production PostgreSQL serializes creations per user, including the count limit.
            # DB uniqueness is the final guard even when SQLite demo tests do not lock rows.
            user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user:
                raise HTTPException(401, "session_expired")
            if client_id and (existing := await db.scalar(query)):
                return check_creation(existing, request_hash)
            await limit()
            count = await db.scalar(
                select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
            )
            if count >= 100:
                raise HTTPException(409, "conversations_limit")
            conversation = Conversation(
                user_id=user_id,
                client_conversation_id=client_id,
                initial_preferences_hash=request_hash,
                preferences=body.preferences.model_dump(),
            )
            db.add(conversation)
            await db.flush()
        return conversation
    except IntegrityError:
        if client_id:
            async with database.sessions() as db:
                existing = await db.scalar(query)
            if existing:
                return check_creation(existing, request_hash)
        raise
