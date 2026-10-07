import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.api.auth import client_key, current_user, digest, rate_limit, token_pair
from app.domain.models import Session, User, utcnow
from app.domain.recovery import RecoveryCredential
from app.domain.schemas import API_ERROR_RESPONSES, StrictModel, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["identity"], responses=API_ERROR_RESPONSES)


class RecoveryCodeResponse(StrictModel):
    recovery_code: str = Field(
        min_length=43,
        max_length=43,
        description="Reusable 256-bit account credential. Shown only upon creation/rotation; "
        "keep it private in a password manager. Rotation invalidates the previous code.",
    )


class RecoveryRequest(StrictModel):
    recovery_code: SecretStr = Field(min_length=1, max_length=128)


@router.post("/recovery-code", response_model=RecoveryCodeResponse)
async def create_recovery_code(
    request: Request, response: Response, user_id: str = Depends(current_user)
):
    """Create or rotate the account credential without changing device sessions."""
    await rate_limit(request, "recovery-rotate:" + user_id, 5, 3600)
    # SHA-256 is appropriate here because all credentials have 256 random bits;
    # this endpoint never accepts a human-chosen, low-entropy password.
    code = secrets.token_urlsafe(32)
    async with request.app.state.db.sessions.begin() as db:
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise HTTPException(401, "session_expired")
        dialect_insert = postgres_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        statement = dialect_insert(RecoveryCredential).values(
            user_id=user_id, code_hash=digest(code), created_at=utcnow(), updated_at=utcnow()
        )
        # Atomic upsert also protects the first concurrent issuance in SQLite.
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[RecoveryCredential.user_id],
                set_={"code_hash": statement.excluded.code_hash, "updated_at": utcnow()},
            )
        )
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_code": code}


@router.post(
    "/recover",
    response_model=TokenResponse,
    responses={
        401: {
            **API_ERROR_RESPONSES[401],
            "description": "invalid_recovery_code: unknown, rotated or deleted account credential.",
        }
    },
)
async def recover_account(body: RecoveryRequest, request: Request, response: Response):
    """Open an independent device session for the existing user; never merge identities."""
    code_hash = digest(body.recovery_code.get_secret_value().strip())
    # Both keys are digests; neither Redis nor logs receive the account secret.
    await rate_limit(request, "recover-ip:" + client_key(request), 10)
    await rate_limit(request, "recover-code:" + code_hash, 5)
    async with request.app.state.db.sessions.begin() as db:
        # The same query/error is used for unknown, rotated and deleted codes.
        user_id = await db.scalar(
            select(RecoveryCredential.user_id).where(RecoveryCredential.code_hash == code_hash)
        )
        if not user_id:
            raise HTTPException(401, "invalid_recovery_code")
        # Rotation and account deletion lock User before the credential. Keep
        # that order here as inserting a session also takes a FK lock on User.
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise HTTPException(401, "invalid_recovery_code")
        # Recheck under the user lock: a rotation or deletion may have happened
        # after the first lookup. Lock through creation of the new session.
        credential = await db.scalar(
            select(RecoveryCredential)
            .where(
                RecoveryCredential.user_id == user_id,
                RecoveryCredential.code_hash == code_hash,
            )
            .with_for_update()
        )
        if not credential:
            raise HTTPException(401, "invalid_recovery_code")
        access, refresh, fields = token_pair()
        credential.updated_at = utcnow()
        db.add(Session(user_id=credential.user_id, **fields))
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": 3600,
        "user_id": user_id,
    }
