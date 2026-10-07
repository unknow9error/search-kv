import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select, update

from app.domain.models import Session, User, utcnow
from app.domain.recovery import RecoveryCredential
from app.domain.schemas import API_ERROR_RESPONSES, ProfileResponse, RefreshRequest, TokenResponse

router = APIRouter(prefix="/v1", tags=["identity"], responses=API_ERROR_RESPONSES)
bearer = HTTPBearer(auto_error=False)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def rate_limit(request: Request, name: str, limit: int, seconds: int = 60):
    if await request.app.state.coordination.increment("rate:" + name, seconds) > limit:
        raise HTTPException(429, "rate_limited", headers={"Retry-After": str(seconds)})


def client_key(request: Request) -> str:
    # request.client is normalized only by the server's configured trusted proxies.
    return digest(request.client.host if request.client else "unknown")


async def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> str:
    if not credentials or len(credentials.credentials) > 128:
        raise HTTPException(401, "session_expired")
    async with request.app.state.db.sessions() as db:
        session = await db.scalar(
            select(Session).where(
                Session.access_hash == digest(credentials.credentials),
                Session.access_expires_at > utcnow(),
            )
        )
    if not session:
        raise HTTPException(401, "session_expired")
    await rate_limit(request, "user:" + session.user_id, 120)
    return session.user_id


def token_pair():
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    return (
        access,
        refresh,
        {
            "access_hash": digest(access),
            "refresh_hash": digest(refresh),
            "access_expires_at": utcnow() + timedelta(hours=1),
            "refresh_expires_at": utcnow() + timedelta(days=30),
        },
    )


@router.post("/auth/anonymous", status_code=201, response_model=TokenResponse)
async def anonymous(request: Request, response: Response):
    await rate_limit(request, "enroll:" + client_key(request), 10, 3600)
    access, refresh, fields = token_pair()
    async with request.app.state.db.sessions.begin() as db:
        user = User()
        db.add(user)
        await db.flush()
        db.add(Session(user_id=user.id, **fields))
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "user_id": user.id}


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_tokens(body: RefreshRequest, request: Request, response: Response):
    await rate_limit(request, "refresh:" + client_key(request), 60)
    access, refresh, fields = token_pair()
    async with request.app.state.db.sessions.begin() as db:
        user_id = await db.scalar(
            select(Session.user_id).where(
                Session.refresh_hash == digest(body.refresh_token), Session.refresh_expires_at > utcnow()
            )
        )
        if not user_id or not await db.scalar(select(User).where(User.id == user_id).with_for_update()):
            raise HTTPException(401, "session_expired")
        # A conditional UPDATE makes refresh single-use across all API replicas.
        result = await db.execute(
            update(Session)
            .where(
                Session.refresh_hash == digest(body.refresh_token), Session.refresh_expires_at > utcnow()
            )
            .values(**fields)
            .returning(Session.user_id)
        )
        user_id = result.scalar_one_or_none()
        if not user_id:
            raise HTTPException(401, "session_expired")
        # Account recovery follows the advertised retention window after device activity.
        # User is locked before session/credential writes to avoid cascade lock inversions.
        await db.execute(
            update(RecoveryCredential)
            .where(RecoveryCredential.user_id == user_id)
            .values(updated_at=utcnow())
        )
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "user_id": user_id}


@router.get("/me", response_model=ProfileResponse)
async def profile(request: Request, user_id: str = Depends(current_user)):
    return {
        "id": user_id,
        "identity": "anonymous",
        "retention_days": request.app.state.settings.retention_days,
    }


@router.delete("/me", status_code=204)
async def delete_profile(request: Request, user_id: str = Depends(current_user)):
    # FK cascades remove sessions, preferences, conversations, events and favorites.
    async with request.app.state.db.sessions.begin() as db:
        await db.execute(delete(User).where(User.id == user_id))
    return Response(status_code=204)
