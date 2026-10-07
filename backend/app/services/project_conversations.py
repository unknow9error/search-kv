"""Durable, source-backed project selection without model or provider calls."""

import asyncio
import hashlib
import json
import re
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.coordination import Coordination
from app.core.db import Database
from app.domain.models import User, aware, new_id, utcnow
from app.domain.project_models import ProjectConversationRecord, ProjectTurnRecord
from app.domain.project_schemas import (
    ProjectCitation,
    ProjectConversationCreate,
    ProjectConversationHistory,
    ProjectConversationOut,
    ProjectConversationPage,
    ProjectCriteria,
    ProjectHistoryTurn,
    ProjectOut,
    ProjectPage,
    ProjectSearchRequest,
    ProjectTurnRequest,
    ProjectTurnResponse,
)
from app.services.projects import Projects


def _hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _known_mentions(text: str, values: list[str]) -> tuple[list[str], str]:
    """Only literal, unambiguous values published in the current catalog facets."""
    found = []
    remainder = text
    for value in sorted(set(values), key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)")
        if pattern.search(remainder):
            found.append(value)
            remainder = pattern.sub(" ", remainder)
    return sorted(found), remainder


_STARTING_PRICE = re.compile(
    r"(?:начальн(?:ая|ую|ой)\s+цен[аыуе]|стартов(?:ая|ую|ой)\s+цен[аыуе]|"
    r"цен[аы]\s+[«\"]?от[»\"]?)\s*"
    r"(?P<operator>до|не\s+(?:выше|больше|более)|максимум|от|не\s+(?:ниже|меньше|менее))\s*"
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>млн|миллион(?:а|ов)?|миллионов|тыс|тысяч(?:и)?)?"
    r"\s*(?:₸|тенге|kzt)?(?!\w)"
)
_BOILERPLATE = re.compile(
    r"\b(?:покажи|показать|найди|найти|ищу|поиск|жк|новостройки|новостройку|"
    r"жилой|жилые|комплекс|комплексы|город|городе|район|районе|в|во|на|для|мне|"
    r"пожалуйста|с|и)\b"
)


def _parse_starting_price(text: str) -> tuple[dict, str]:
    """A generic apartment budget deliberately never becomes a project's price 'from'."""
    matches = list(_STARTING_PRICE.finditer(text))
    if len(matches) != 1:
        return {}, text
    match = matches[0]
    unit = match["unit"] or ""
    multiplier = 1_000_000 if unit.startswith(("млн", "миллион")) else 1000 if unit else 1
    amount = Decimal(match["amount"].replace(",", ".")) * multiplier
    if amount != amount.to_integral_value() or not 1 <= amount <= 10_000_000_000:
        return {}, text
    minimum = match["operator"] == "от" or bool(re.search(r"ниже|меньше|менее", match["operator"]))
    parsed = {
        "price_mode": "published_starting_price",
        "price_min" if minimum else "price_max": int(amount),
    }
    return parsed, text[: match.start()] + " " + text[match.end() :]


def _unsupported_remainder(text: str) -> bool:
    remainder = _BOILERPLATE.sub(" ", text)
    return bool(re.search(r"[\w\d]", remainder))


def _conversation(row: ProjectConversationRecord) -> ProjectConversationOut:
    return ProjectConversationOut(
        id=row.id,
        title=row.title,
        criteria=ProjectCriteria.model_validate(row.criteria),
        created_at=aware(row.created_at),
        updated_at=aware(row.updated_at),
    )


def _replay(row: ProjectTurnRecord, request_hash: str) -> ProjectTurnResponse:
    if row.request_hash != request_hash:
        raise HTTPException(409, "idempotency_conflict")
    if row.state != "complete" or not row.response:
        raise HTTPException(409, "turn_in_progress")
    return ProjectTurnResponse.model_validate(row.response)


def _citations(projects: list[ProjectOut]) -> list[ProjectCitation]:
    citations = []
    seen = set()
    for project in projects:
        evidence = [
            (f"ЖК {project.name} — {project.provider_name}", project.source_url, project.observed_at)
        ]
        for name, price in (
            ("Опубликованная начальная цена", project.published_starting_price),
            ("Минимум наблюдавшихся лотов", project.observed_listing_minimum),
        ):
            if price is not None and price.kind != "unknown":
                evidence.append((f"{project.name}: {name}", price.source_url, price.observed_at))
        for title, url, observed_at in evidence:
            key = (project.id, url, aware(observed_at).isoformat())
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                ProjectCitation(
                    project_id=project.id,
                    title=title,
                    url=url,
                    observed_at=aware(observed_at),
                    demo=project.provenance == "demo",
                )
            )
    return citations


def _compact_project_snapshot(project: dict) -> dict:
    """Keep recorded card facts, while omitting detail-only children from history."""
    images = project.get("images") or []
    image = next(
        (item for item in images if item.get("kind") in {"facade", "site"}),
        images[0] if images else None,
    )
    return {
        **project,
        "layouts": [],
        "documents": [],
        "buildings": [],
        "images": [image] if image is not None else [],
    }


def _compact_response_snapshot(response: dict) -> ProjectTurnResponse:
    # Clone before reducing stored snapshots. Idempotent turn replay must still
    # return the exact durable response, including any older rich detail payload.
    payload = dict(response)
    if payload.get("results") is not None:
        payload["results"] = {
            **payload["results"],
            "items": [_compact_project_snapshot(item) for item in payload["results"]["items"]],
        }
    if payload.get("comparison") is not None:
        payload["comparison"] = {
            **payload["comparison"],
            "projects": [_compact_project_snapshot(item) for item in payload["comparison"]["projects"]],
        }
    return ProjectTurnResponse.model_validate(payload)


class ProjectConversations:
    """An independent project dialogue; the legacy apartment/SSE chat is unchanged.

    A turn computes only against the local public-data snapshot. Its criteria and complete
    response are committed together, so failed requests cannot partially change the search.
    Completed retries replay the saved response even if the public catalog has changed.
    """

    def __init__(
        self,
        db: Database,
        coordination: Coordination,
        projects: Projects,
        settings: Settings,
    ):
        self.db, self.coordination, self.projects, self.settings = db, coordination, projects, settings

    @staticmethod
    async def _owned(db, user_id: str, conversation_id: str, *, lock: bool = False):
        query = select(ProjectConversationRecord).where(
            ProjectConversationRecord.id == conversation_id,
            ProjectConversationRecord.user_id == user_id,
        )
        if lock:
            query = query.with_for_update()
        row = await db.scalar(query)
        if row is None:
            raise HTTPException(404, "conversation_not_found")
        return row

    @staticmethod
    async def _turn(db, conversation_id: str, client_turn_id: str):
        return await db.scalar(
            select(ProjectTurnRecord).where(
                ProjectTurnRecord.conversation_id == conversation_id,
                ProjectTurnRecord.client_turn_id == client_turn_id,
            )
        )

    async def create(self, user_id: str, body: ProjectConversationCreate) -> ProjectConversationOut:
        # PostgreSQL user-row locking is the durable production guard. The same
        # user-scoped lease also protects the count limit on SQLite/local builds.
        # Briefly wait so concurrent identical client retries replay a single row.
        key = "project-conversation-create:" + user_id
        token = None
        for _ in range(100):
            token = await self.coordination.acquire(key, 120)
            if token is not None:
                break
            await asyncio.sleep(0.01)
        if token is None:
            raise HTTPException(409, "conversation_creation_in_progress")
        try:
            return await self._create(user_id, body)
        finally:
            await asyncio.shield(self.coordination.release(key, token))

    async def _create(self, user_id: str, body: ProjectConversationCreate) -> ProjectConversationOut:
        client_id = str(body.client_conversation_id) if body.client_conversation_id else None
        criteria = body.criteria.model_dump(mode="json")
        request_hash = _hash(criteria)
        query = select(ProjectConversationRecord).where(
            ProjectConversationRecord.user_id == user_id,
            ProjectConversationRecord.client_conversation_id == client_id,
        )

        def check(row):
            if row.initial_criteria_hash != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return _conversation(row)

        try:
            async with self.db.sessions.begin() as db:
                # Serialize the creation limit per real user on PostgreSQL. Uniqueness is
                # the final retry guard, including when SQLite has no row-level locks.
                user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
                if user is None:
                    raise HTTPException(401, "session_expired")
                if client_id and (existing := await db.scalar(query)):
                    return check(existing)
                count = await db.scalar(
                    select(func.count())
                    .select_from(ProjectConversationRecord)
                    .where(ProjectConversationRecord.user_id == user_id)
                )
                if count >= 100:
                    raise HTTPException(409, "conversations_limit")
                row = ProjectConversationRecord(
                    user_id=user_id,
                    client_conversation_id=client_id,
                    initial_criteria_hash=request_hash,
                    criteria=criteria,
                )
                db.add(row)
                await db.flush()
                result = _conversation(row)
            return result
        except IntegrityError:
            if client_id:
                async with self.db.sessions() as db:
                    existing = await db.scalar(query)
                if existing is not None:
                    return check(existing)
            raise

    async def list(self, user_id: str) -> ProjectConversationPage:
        async with self.db.sessions() as db:
            rows = (
                await db.scalars(
                    select(ProjectConversationRecord)
                    .where(ProjectConversationRecord.user_id == user_id)
                    .order_by(
                        ProjectConversationRecord.updated_at.desc(), ProjectConversationRecord.id.desc()
                    )
                    .limit(100)
                )
            ).all()
        return ProjectConversationPage(items=[_conversation(row) for row in rows])

    async def history(
        self,
        user_id: str,
        conversation_id: UUID | str,
        before: UUID | str | None = None,
    ) -> ProjectConversationHistory:
        conversation_id = str(conversation_id)
        async with self.db.sessions() as db:
            row = await self._owned(db, user_id, conversation_id)
            # Read just the response metadata for the timeline. Selecting the full
            # JSON column here would decode 30 old project-detail trees only to drop
            # them, making rich saved conversations unnecessarily expensive to read.
            response_fields = {
                name: ProjectTurnRecord.response[name].label("response_" + name)
                for name in ProjectTurnResponse.model_fields
                if name not in {"results", "comparison"}
            }
            query = select(
                ProjectTurnRecord.id,
                ProjectTurnRecord.client_turn_id,
                ProjectTurnRecord.message,
                ProjectTurnRecord.state,
                ProjectTurnRecord.created_at,
                *response_fields.values(),
            ).where(ProjectTurnRecord.conversation_id == conversation_id)
            if before is not None:
                anchor = (
                    await db.execute(
                        select(
                            ProjectTurnRecord.conversation_id,
                            ProjectTurnRecord.created_at,
                            ProjectTurnRecord.id,
                        ).where(ProjectTurnRecord.id == str(before))
                    )
                ).first()
                if anchor is None or anchor.conversation_id != conversation_id:
                    raise HTTPException(404, "turn_not_found")
                query = query.where(
                    or_(
                        ProjectTurnRecord.created_at < anchor.created_at,
                        and_(
                            ProjectTurnRecord.created_at == anchor.created_at,
                            ProjectTurnRecord.id < anchor.id,
                        ),
                    )
                )
            page = (
                await db.execute(
                    query.order_by(
                        ProjectTurnRecord.created_at.desc(), ProjectTurnRecord.id.desc()
                    ).limit(31)
                )
            ).all()
            # Restore only the latest catalog-bearing snapshot in this page. A later
            # clarification keeps its message without losing the last visible results.
            snapshot = (
                (
                    await db.execute(
                        select(ProjectTurnRecord.id, ProjectTurnRecord.response)
                        .where(
                            ProjectTurnRecord.id.in_([turn.id for turn in page[:30]]),
                            or_(
                                ProjectTurnRecord.response["results"].as_string().is_not(None),
                                ProjectTurnRecord.response["comparison"].as_string().is_not(None),
                            ),
                        )
                        .order_by(ProjectTurnRecord.created_at.desc(), ProjectTurnRecord.id.desc())
                        .limit(1)
                    )
                ).first()
                if page
                else None
            )
        has_more = len(page) > 30
        turns = list(reversed(page[:30]))
        latest_snapshot = _compact_response_snapshot(snapshot.response) if snapshot else None

        def history_response(turn):
            if snapshot and turn.id == snapshot.id:
                return latest_snapshot
            payload = {
                name: turn._mapping["response_" + name]
                for name in response_fields
                if turn._mapping["response_" + name] is not None
            }
            return ProjectTurnResponse.model_validate(payload) if payload.get("turn_id") else None

        return ProjectConversationHistory(
            **_conversation(row).model_dump(),
            has_more=has_more,
            next_before=turns[0].id if has_more else None,
            turns=[
                ProjectHistoryTurn(
                    id=turn.id,
                    client_turn_id=turn.client_turn_id,
                    message=turn.message,
                    state=turn.state,
                    created_at=aware(turn.created_at),
                    response=history_response(turn),
                )
                for turn in turns
            ],
        )

    async def delete(self, user_id: str, conversation_id: UUID | str) -> None:
        conversation_id = str(conversation_id)
        async with self.db.sessions() as db:
            await self._owned(db, user_id, conversation_id)
        key = "project-conversation:" + conversation_id
        token = await self.coordination.acquire(key, 120)
        if token is None:
            raise HTTPException(409, "conversation_busy")
        try:
            async with self.db.sessions.begin() as db:
                await self._owned(db, user_id, conversation_id, lock=True)
                await db.execute(
                    delete(ProjectConversationRecord).where(
                        ProjectConversationRecord.id == conversation_id,
                        ProjectConversationRecord.user_id == user_id,
                    )
                )
        finally:
            await asyncio.shield(self.coordination.release(key, token))

    async def _criteria_from_message(
        self, criteria: ProjectCriteria, message: str
    ) -> tuple[ProjectCriteria, bool]:
        text = message.casefold()
        facets = await self.projects.facets()
        cities, text = _known_mentions(text, [value.value for value in facets.cities])
        if len(cities) > 1:
            return criteria, False
        updates = {"city": cities[0]} if cities else {}
        city = updates.get("city", criteria.city)
        if city:
            facets = await self.projects.facets(city)
        districts, text = _known_mentions(text, [value.value for value in facets.districts])
        if districts:
            updates["districts"] = districts
        price, text = _parse_starting_price(text)
        updates.update(price)
        if _unsupported_remainder(text):
            return criteria, False
        # An explicit city change starts a new location scope; the previous map view
        # and a district from a different city must not constrain the new search.
        if cities and cities[0] != criteria.city:
            updates["bounds"] = None
            if not districts:
                updates["districts"] = []
        try:
            return ProjectCriteria.model_validate({**criteria.model_dump(mode="json"), **updates}), True
        except ValueError:
            return criteria, False

    async def _response(
        self,
        conversation_id: str,
        turn_id: str,
        body: ProjectTurnRequest,
        initial_criteria: ProjectCriteria,
    ) -> ProjectTurnResponse:
        criteria = body.criteria or initial_criteria
        selected_ids = [str(project_id) for project_id in body.selected_project_ids]
        selected = await self.projects.get_many(selected_ids)
        if {project.id for project in selected} != set(selected_ids):
            raise HTTPException(404, "project_not_found")
        action = body.action
        if action == "search" and body.criteria is None:
            criteria, understood = await self._criteria_from_message(criteria, body.message)
            if not understood:
                return ProjectTurnResponse(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    client_turn_id=body.client_turn_id,
                    action="clarify",
                    message="Базовый помощник понимает точные названия города и района, явно обозначенную начальную цену ЖК и структурированные фильтры. Задайте остальные условия через фильтры.",
                    criteria=initial_criteria,
                    unsupported_conditions=[
                        "Не все условия сообщения распознаны; критерии поиска сохранены без изменений."
                    ],
                    suggestions=["Открыть фильтры", "Указать точное название города или района"],
                    created_at=utcnow(),
                )
        comparison = None
        results = None
        if action == "compare":
            try:
                comparison = await self.projects.compare([project.id for project in selected])
            except ValueError as error:
                raise HTTPException(404, "project_not_found") from error
            evidence = comparison.projects
            message = f"Сравнение {len(evidence)} ЖК по сохранённым публичным сведениям. Неизвестные значения оставлены пустыми. Начальная цена и минимум наблюдавшихся лотов показываются отдельно."
        elif action == "explain":
            results = ProjectPage(
                items=selected,
                total=len(selected),
                criteria=criteria,
                sort="observed_desc",
                unknown_coordinates_count=sum(
                    project.latitude is None or project.longitude is None for project in selected
                ),
            )
            evidence = selected
            message = "Сведения о выбранных ЖК взяты из публичных источников. Планировки и наблюдавшиеся лоты не подтверждают текущее наличие квартир. Источник и дата приведены рядом с фактами."
        else:
            results = await self.projects.search(ProjectSearchRequest(criteria=criteria, limit=20))
            evidence = results.items
            if results.total:
                message = (
                    f"В сохранённых публичных данных найдено {results.total} ЖК по заданным фильтрам. "
                )
            else:
                message = "По этим фильтрам в сохранённых публичных данных ЖК не найдены. Это не доказывает отсутствие ЖК или квартир в продаже. "
            message += (
                "Для цены выбран минимум наблюдавшихся лотов — он не означает начальную цену всего ЖК."
                if criteria.price_mode == "observed_listing_minimum"
                else "Опубликованная начальная цена и минимум наблюдавшихся лотов показываются отдельно; неизвестная цена остаётся неизвестной."
            )
        return ProjectTurnResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            client_turn_id=body.client_turn_id,
            action=action,
            message=message,
            criteria=criteria,
            results=results,
            comparison=comparison,
            citations=_citations(evidence),
            suggestions=["Открыть фильтры", "Выбрать 2–3 ЖК для сравнения"],
            created_at=utcnow(),
        )

    async def turn(
        self, user_id: str, conversation_id: UUID | str, body: ProjectTurnRequest
    ) -> ProjectTurnResponse:
        conversation_id = str(conversation_id)
        client_turn_id = str(body.client_turn_id)
        request_hash = _hash(body.model_dump(mode="json"))
        async with self.db.sessions() as db:
            await self._owned(db, user_id, conversation_id)
            existing = await self._turn(db, conversation_id, client_turn_id)
            if existing is not None:
                return _replay(existing, request_hash)
        key = "project-conversation:" + conversation_id
        token = await self.coordination.acquire(key, 120)
        if token is None:
            raise HTTPException(409, "turn_in_progress")
        try:
            # Recheck after acquire: a concurrent request may have completed or a
            # deletion may have won after the preflight read.
            async with self.db.sessions() as db:
                conversation = await self._owned(db, user_id, conversation_id)
                existing = await self._turn(db, conversation_id, client_turn_id)
                if existing is not None:
                    return _replay(existing, request_hash)
                count = await db.scalar(
                    select(func.count())
                    .select_from(ProjectTurnRecord)
                    .where(ProjectTurnRecord.conversation_id == conversation_id)
                )
                if count >= 200:
                    raise HTTPException(409, "conversation_limit")
                initial = ProjectCriteria.model_validate(conversation.criteria)
            turn_id = new_id()
            response = await self._response(conversation_id, turn_id, body, initial)
            async with self.db.sessions.begin() as db:
                conversation = await self._owned(db, user_id, conversation_id, lock=True)
                existing = await self._turn(db, conversation_id, client_turn_id)
                if existing is not None:
                    return _replay(existing, request_hash)
                if ProjectCriteria.model_validate(conversation.criteria) != initial:
                    raise HTTPException(409, "conversation_changed")
                count = await db.scalar(
                    select(func.count())
                    .select_from(ProjectTurnRecord)
                    .where(ProjectTurnRecord.conversation_id == conversation_id)
                )
                if count >= 200:
                    raise HTTPException(409, "conversation_limit")
                conversation.criteria = response.criteria.model_dump(mode="json")
                conversation.updated_at = response.created_at
                if not count:
                    conversation.title = body.message[:100]
                db.add(
                    ProjectTurnRecord(
                        id=turn_id,
                        conversation_id=conversation_id,
                        client_turn_id=client_turn_id,
                        message=body.message,
                        request_hash=request_hash,
                        response=response.model_dump(mode="json"),
                        state="complete",
                        created_at=response.created_at,
                    )
                )
                await db.flush()
            return response
        except IntegrityError:
            async with self.db.sessions() as db:
                await self._owned(db, user_id, conversation_id)
                existing = await self._turn(db, conversation_id, client_turn_id)
            if existing is not None:
                return _replay(existing, request_hash)
            raise
        finally:
            await asyncio.shield(self.coordination.release(key, token))
