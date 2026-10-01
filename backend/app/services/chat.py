import asyncio
import hashlib
import json
import logging
import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.core.coordination import Coordination
from app.core.db import Database
from app.core.telemetry import TURNS
from app.domain.models import Conversation, Turn, TurnEvent, aware, new_id, utcnow
from app.domain.schemas import Preferences, TurnRequest
from app.services.assistant import Assistant, clarification, explain_listings
from app.services.catalog import Catalog

log = logging.getLogger("meken")


def sse(sequence: int, kind: str, payload: dict) -> str:
    return f"id: {sequence}\nevent: {kind}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


class Chat:
    def __init__(self, db: Database, coordination: Coordination, catalog: Catalog, assistant: Assistant):
        self.db, self.coordination, self.catalog, self.assistant = db, coordination, catalog, assistant

    async def conversation(self, conversation_id: str, user_id: str) -> Conversation:
        async with self.db.sessions() as db:
            row = await db.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.user_id == user_id
                )
            )
        if not row:
            raise HTTPException(404, "conversation_not_found")
        return row

    async def reserve(self, conversation_id: str, user_id: str, body: TurnRequest):
        await self.conversation(conversation_id, user_id)
        request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        async with self.db.sessions() as db:
            existing = await db.scalar(
                select(Turn).where(
                    Turn.conversation_id == conversation_id,
                    Turn.client_turn_id == str(body.client_turn_id),
                )
            )
            count = await db.scalar(
                select(func.count()).select_from(Turn).where(Turn.conversation_id == conversation_id)
            )
        if existing:
            if existing.message != body.message or (
                existing.request_hash and existing.request_hash != request_hash
            ):
                raise HTTPException(409, "idempotency_conflict")
            return existing.id, None
        if count >= 200:
            raise HTTPException(409, "conversation_limit")
        key = "conversation:" + conversation_id
        lease = await self.coordination.acquire(key, 180)
        if lease is None:
            raise HTTPException(409, "turn_in_progress")
        try:
            turn_id = new_id()
            async with self.db.sessions.begin() as db:
                db.add(
                    Turn(
                        id=turn_id,
                        conversation_id=conversation_id,
                        client_turn_id=str(body.client_turn_id),
                        message=body.message,
                        request_hash=request_hash,
                    )
                )
            return turn_id, lease
        except Exception:
            await self.coordination.release(key, lease)
            raise

    async def append(self, turn_id: str, sequence: int, kind: str, payload: dict) -> str:
        async with self.db.sessions.begin() as db:
            db.add(TurnEvent(turn_id=turn_id, sequence=sequence, kind=kind, payload=payload))
            if kind == "listings":
                conversation_id = (
                    select(Turn.conversation_id).where(Turn.id == turn_id).scalar_subquery()
                )
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(last_listing_ids=[item["id"] for item in payload["items"]])
                )
        return sse(sequence, kind, payload)

    async def replay(self, turn_id: str, after: int = 0):
        # Never re-runs LLM or provider work. Replay is a durable event read.
        async with self.db.sessions() as db:
            turn = await db.get(Turn, turn_id)
            if (
                turn
                and turn.status == "running"
                and aware(turn.created_at) < utcnow() - timedelta(seconds=180)
            ):
                turn.status = "interrupted"
                await db.commit()
            events = (
                await db.scalars(
                    select(TurnEvent)
                    .where(TurnEvent.turn_id == turn_id, TurnEvent.sequence > after)
                    .order_by(TurnEvent.sequence)
                )
            ).all()
        for event in events:
            yield sse(event.sequence, event.kind, event.payload)
        if not turn:
            return
        if turn.status == "running":
            yield "event: pending\ndata: {}\n\n"
        elif not events or events[-1].kind != "done":
            yield (
                "event: done\ndata: " + json.dumps({"status": turn.status, "turn_id": turn_id}) + "\n\n"
            )

    async def stream(
        self, conversation_id: str, user_id: str, body: TurnRequest, turn_id: str, lease: str
    ):
        sequence, outcome = 0, "interrupted"

        async def emit(kind, payload):
            nonlocal sequence
            sequence += 1
            return await self.append(turn_id, sequence, kind, payload)

        try:
            async with asyncio.timeout(120):
                yield await emit("accepted", {"turn_id": turn_id, "conversation_id": conversation_id})
                yield await emit("status", {"text": "Разбираюсь в ваших пожеланиях"})
                conversation = await self.conversation(conversation_id, user_id)
                prefs = Preferences.model_validate(conversation.preferences)
                selected_ids = [str(x) for x in body.selected_listing_ids]
                if not selected_ids:
                    for index, pattern in enumerate(
                        [r"перв(?:ая|ую|ой)", r"втор(?:ая|ую|ой)", r"треть(?:я|ю|ей)"]
                    ):
                        if (
                            re.search(pattern, body.message.casefold())
                            and len(conversation.last_listing_ids) > index
                        ):
                            selected_ids = [conversation.last_listing_ids[index]]
                            break
                    if not selected_ids and "сравни" in body.message.casefold():
                        selected_ids = conversation.last_listing_ids[:3]
                decision, degraded = await self.assistant.plan(
                    user_id, body.message, prefs, bool(selected_ids)
                )
                prefs = decision.preferences
                async with self.db.sessions.begin() as db:
                    await db.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation_id)
                        .values(
                            preferences=prefs.model_dump(),
                            updated_at=utcnow(),
                            title=body.message[:80]
                            if not conversation.preferences
                            else conversation.title,
                        )
                    )
                yield await emit("preferences", prefs.model_dump())
                if degraded:
                    yield await emit(
                        "notice",
                        {
                            "text": "Сейчас доступен базовый подбор. Если я не учёл пожелание, задайте его через фильтры."
                        },
                    )
                if decision.action in {"explain", "compare"}:
                    listings = []
                    for apartment_id in selected_ids:
                        listing = await self.catalog.get(str(apartment_id), prefs)
                        if listing:
                            listings.append(listing)
                    yield await emit(
                        "message",
                        {
                            "text": explain_listings(listings),
                            "citations": [
                                {
                                    "id": x.id,
                                    "title": x.complex_name,
                                    "url": x.source_url,
                                    "demo": x.provenance == "demo",
                                }
                                for x in listings
                            ],
                        },
                    )
                elif decision.action == "knowledge":
                    yield await emit(
                        "message",
                        await self.assistant.knowledge(
                            user_id, decision.knowledge_query or body.message
                        ),
                    )
                elif decision.action == "unsupported":
                    yield await emit(
                        "message",
                        {
                            "text": "Я помогаю искать и сравнивать квартиры. Для бронирования или оформления покупки нужно обратиться к застройщику.",
                            "citations": [],
                        },
                    )
                elif decision.action == "clarify" or not prefs.city:
                    text, suggestions = clarification(prefs, decision.reason)
                    yield await emit("message", {"text": text, "citations": []})
                    yield await emit("suggestions", {"items": suggestions})
                else:
                    listings = await self.catalog.search(prefs)
                    yield await emit(
                        "listings",
                        {
                            "items": [x.model_dump(mode="json") for x in listings],
                            "phase": "catalog",
                            "replace": True,
                        },
                    )
                    yield await emit("status", {"text": "Уточняю предложения у застройщиков"})
                    failures, providers = 0, 0
                    async for progress in self.catalog.refresh_events(prefs.city, prefs):
                        providers += progress["status"] != "batch"
                        if progress["status"] not in {"complete", "recently_checked", "batch"}:
                            failures += 1
                        yield await emit("provider", progress)
                        listings = await self.catalog.search(prefs)
                        yield await emit(
                            "listings",
                            {
                                "items": [x.model_dump(mode="json") for x in listings],
                                "phase": "refresh",
                                "replace": True,
                            },
                        )
                    async with self.db.sessions.begin() as db:
                        await db.execute(
                            update(Conversation)
                            .where(Conversation.id == conversation_id)
                            .values(last_listing_ids=[x.id for x in listings])
                        )
                    if listings:
                        text = f"В подборке {len(listings)} вариантов по вашим условиям. Откройте квартиру, чтобы посмотреть детали и уточнить наличие."
                        if not prefs.budget_max:
                            text += " Пока ищу без ограничения бюджета — назовите удобную общую сумму, чтобы сузить выбор."
                    else:
                        text = "По этим условиям пока нет квартир в доступной выдаче. Можно изменить бюджет, количество комнат или обязательные условия."
                    if failures:
                        text += (
                            " Часть предложений пока не удалось уточнить: подборка может быть неполной."
                        )
                    if not providers:
                        text += " В этом городе пока нет подключённых источников."
                    yield await emit("message", {"text": text, "citations": []})
                    yield await emit(
                        "suggestions",
                        {"items": ["Нужны 2 комнаты", "Хочу школу рядом", "Бюджет до 35 млн ₸"]},
                    )
                outcome = "complete"
                yield await emit("done", {"status": outcome, "turn_id": turn_id})
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = "failed"
            log.error("chat_turn_failed")
            yield await emit(
                "error",
                {
                    "code": "turn_failed",
                    "text": "Не удалось завершить подбор. Уже полученные варианты сохранены. Попробуйте ещё раз.",
                },
            )
            yield await emit("done", {"status": outcome, "turn_id": turn_id})
        finally:

            async def cleanup():
                async with self.db.sessions.begin() as db:
                    await db.execute(update(Turn).where(Turn.id == turn_id).values(status=outcome))
                await self.coordination.release("conversation:" + conversation_id, lease)
                TURNS.labels(outcome).inc()

            await asyncio.shield(cleanup())
