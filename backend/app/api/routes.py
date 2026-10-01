from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from app.api.auth import current_user, rate_limit
from app.domain.models import Conversation, Favorite, Turn, TurnEvent, aware
from app.domain.schemas import (
    AppConfiguration,
    ConversationRequest,
    Listing,
    ListingPage,
    Preferences,
    SearchRequest,
    TurnRequest,
    VerificationResponse,
)

router = APIRouter(prefix="/v1", tags=["discovery"])


@router.get("/config", response_model=AppConfiguration)
async def config(request: Request):
    settings = request.app.state.settings
    providers = list(request.app.state.catalog.providers.values())
    return {
        "mode": "demo" if settings.env == "demo" else "live",
        "ai_enabled": settings.ai_enabled,
        "cities": sorted({c for p in providers for c in p.cities}, key=lambda c: (c != "Астана", c)),
        "privacy_url": settings.privacy_url or None,
        "terms_url": settings.terms_url or None,
        "retention_days": settings.retention_days,
    }


@router.post("/search", response_model=ListingPage)
async def search(body: SearchRequest, request: Request, user_id: str = Depends(current_user)):
    # Pure database query; no model call and no per-listing upstream request.
    rows = await request.app.state.catalog.search(body.preferences, body.limit)
    return {"items": [r.model_dump(mode="json") for r in rows]}


@router.get("/apartments/{apartment_id}", response_model=Listing)
async def detail(apartment_id: UUID, request: Request, user_id: str = Depends(current_user)):
    listing = await request.app.state.catalog.get(str(apartment_id))
    if not listing:
        raise HTTPException(404, "apartment_not_found")
    return listing


@router.post("/apartments/{apartment_id}/verify", response_model=VerificationResponse)
async def verify(apartment_id: UUID, request: Request, user_id: str = Depends(current_user)):
    await rate_limit(request, "verify:" + user_id, 12)
    result = await request.app.state.catalog.verify(str(apartment_id))
    if not result:
        raise HTTPException(404, "apartment_not_found")
    return result


@router.get("/favorites", response_model=ListingPage)
async def favorites(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db.sessions() as db:
        ids = (
            await db.scalars(
                select(Favorite.apartment_id)
                .where(Favorite.user_id == user_id)
                .order_by(Favorite.created_at.desc())
                .limit(200)
            )
        ).all()
    items = []
    for apartment_id in ids:
        listing = await request.app.state.catalog.get(apartment_id)
        if listing:
            items.append(listing)
    return {"items": items}


@router.put("/favorites/{apartment_id}", status_code=204)
async def add_favorite(apartment_id: UUID, request: Request, user_id: str = Depends(current_user)):
    if not await request.app.state.catalog.get(str(apartment_id)):
        raise HTTPException(404, "apartment_not_found")
    try:
        async with request.app.state.db.sessions.begin() as db:
            if await db.get(Favorite, (user_id, str(apartment_id))):
                return Response(status_code=204)
            count = await db.scalar(
                select(func.count()).select_from(Favorite).where(Favorite.user_id == user_id)
            )
            if count >= 200:
                raise HTTPException(409, "favorites_limit")
            db.add(Favorite(user_id=user_id, apartment_id=str(apartment_id)))
    except IntegrityError:
        pass  # Concurrent identical PUT is idempotent.
    return Response(status_code=204)


@router.delete("/favorites/{apartment_id}", status_code=204)
async def remove_favorite(apartment_id: UUID, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db.sessions.begin() as db:
        await db.execute(
            delete(Favorite).where(
                Favorite.user_id == user_id, Favorite.apartment_id == str(apartment_id)
            )
        )
    return Response(status_code=204)


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationRequest, request: Request, user_id: str = Depends(current_user)
):
    await rate_limit(request, "conversation-create:" + user_id, 10)
    async with request.app.state.db.sessions.begin() as db:
        count = await db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
        )
        if count >= 100:
            raise HTTPException(409, "conversations_limit")
        conversation = Conversation(user_id=user_id, preferences=body.preferences.model_dump())
        db.add(conversation)
        await db.flush()
    return {"id": conversation.id, "title": conversation.title, "preferences": conversation.preferences}


@router.get("/conversations")
async def conversations(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db.sessions() as db:
        rows = (
            await db.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(100)
            )
        ).all()
    return {
        "items": [
            {
                "id": x.id,
                "title": x.title,
                "preferences": x.preferences,
                "updated_at": aware(x.updated_at),
            }
            for x in rows
        ]
    }


@router.put("/conversations/{conversation_id}/preferences")
async def update_preferences(
    conversation_id: UUID, body: Preferences, request: Request, user_id: str = Depends(current_user)
):
    from sqlalchemy import update

    from app.core.coordination import BusyError
    from app.domain.models import utcnow

    await request.app.state.chat.conversation(str(conversation_id), user_id)
    try:
        async with request.app.state.coordination.lease("conversation:" + str(conversation_id), 15):
            async with request.app.state.db.sessions.begin() as db:
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == str(conversation_id))
                    .values(preferences=body.model_dump(), updated_at=utcnow())
                )
    except BusyError:
        raise HTTPException(409, "turn_in_progress") from None
    return body


@router.get("/conversations/{conversation_id}")
async def history(
    conversation_id: UUID,
    request: Request,
    before: UUID | None = None,
    user_id: str = Depends(current_user),
):
    conversation = await request.app.state.chat.conversation(str(conversation_id), user_id)
    async with request.app.state.db.sessions() as db:
        query = select(Turn).where(Turn.conversation_id == str(conversation_id))
        if before:
            anchor = await db.get(Turn, str(before))
            if not anchor or anchor.conversation_id != str(conversation_id):
                raise HTTPException(404, "turn_not_found")
            query = query.where(Turn.created_at < anchor.created_at)
        page = list((await db.scalars(query.order_by(Turn.created_at.desc()).limit(31))).all())
        has_more = len(page) > 30
        turns = list(reversed(page[:30]))
        turn_ids = [t.id for t in turns]
        events = (
            list(
                (
                    await db.scalars(
                        select(TurnEvent)
                        .where(
                            TurnEvent.turn_id.in_(turn_ids),
                            TurnEvent.kind.in_(["message", "preferences", "done", "notice"]),
                        )
                        .order_by(TurnEvent.sequence)
                    )
                ).all()
            )
            if turns
            else []
        )
        # Only the most recent result set is needed to restore the screen.
        if turns:
            latest_result = await db.scalar(
                select(TurnEvent)
                .join(Turn)
                .where(Turn.id.in_(turn_ids), TurnEvent.kind == "listings")
                .order_by(Turn.created_at.desc(), TurnEvent.sequence.desc())
                .limit(1)
            )
            if latest_result:
                events.append(latest_result)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "preferences": conversation.preferences,
        "has_more": has_more,
        "next_before": turns[0].id if has_more else None,
        "turns": [
            {
                "id": t.id,
                "message": t.message,
                "status": t.status,
                "events": [
                    {"sequence": e.sequence, "kind": e.kind, "payload": e.payload}
                    for e in sorted(events, key=lambda e: e.sequence)
                    if e.turn_id == t.id
                ],
            }
            for t in turns
        ],
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID, request: Request, user_id: str = Depends(current_user)
):
    await request.app.state.chat.conversation(str(conversation_id), user_id)
    if await request.app.state.coordination.get("conversation:" + str(conversation_id)):
        raise HTTPException(409, "turn_in_progress")
    async with request.app.state.db.sessions.begin() as db:
        await db.execute(
            delete(Conversation).where(
                Conversation.id == str(conversation_id), Conversation.user_id == user_id
            )
        )
    return Response(status_code=204)


STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Content-Encoding": "identity"}


@router.post(
    "/conversations/{conversation_id}/turns", responses={200: {"content": {"text/event-stream": {}}}}
)
async def send_turn(
    conversation_id: UUID, body: TurnRequest, request: Request, user_id: str = Depends(current_user)
):
    await rate_limit(request, "turn:" + user_id, 15)
    chat = request.app.state.chat
    turn_id, lease = await chat.reserve(str(conversation_id), user_id, body)
    stream = (
        chat.stream(str(conversation_id), user_id, body, turn_id, lease)
        if lease
        else chat.replay(turn_id)
    )
    return StreamingResponse(
        stream, media_type="text/event-stream", headers={**STREAM_HEADERS, "X-Turn-ID": turn_id}
    )


@router.get("/conversations/{conversation_id}/turns/{turn_id}/events")
async def replay(
    conversation_id: UUID,
    turn_id: UUID,
    request: Request,
    after: int = Query(0, ge=0, le=10000),
    user_id: str = Depends(current_user),
):
    await request.app.state.chat.conversation(str(conversation_id), user_id)
    async with request.app.state.db.sessions() as db:
        turn = await db.get(Turn, str(turn_id))
    if not turn or turn.conversation_id != str(conversation_id):
        raise HTTPException(404, "turn_not_found")
    return StreamingResponse(
        request.app.state.chat.replay(str(turn_id), after),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )
