import asyncio
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.config import Settings
from app.core.coordination import Coordination
from app.core.db import Database
from app.domain.models import Apartment, Conversation, Favorite, Provider, User, utcnow
from app.domain.project_models import (
    ProjectConversationRecord,
    ProjectFavorite,
    ProjectRecord,
    ProjectTurnRecord,
)
from app.domain.project_schemas import (
    ProjectConversationCreate,
    ProjectCriteria,
    ProjectInput,
    ProjectTurnRequest,
    ProjectTurnResponse,
)
from app.services.project_conversations import ProjectConversations
from app.services.projects import Projects, project_id, upsert_public_projects


@pytest.fixture
async def project_chat(tmp_path):
    """Real local database and catalog; no application .env, source or model calls."""
    database_url = f"sqlite+aiosqlite:///{tmp_path}/project-chat.sqlite3"
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=database_url,
        redis_url="",
        ai_enabled=False,
        fresh_seconds=60,
    )
    db = Database(database_url)
    coordination = Coordination("")
    catalog = SimpleNamespace(
        providers={"public": SimpleNamespace(id="public", public_data=True, demo=False)}
    )
    projects = Projects(db, coordination, catalog, settings)
    service = ProjectConversations(db, coordination, projects, settings)
    user, other = str(uuid4()), str(uuid4())
    observed = utcnow() - timedelta(seconds=5)
    apartment_id = str(uuid4())
    await db.create_schema()
    async with db.sessions.begin() as session:
        session.add_all(
            [
                User(id=user),
                User(id=other),
                Provider(id="public", name="Public source", demo=False, enabled=True),
            ]
        )
        await session.flush()
        await upsert_public_projects(
            session,
            "public",
            [
                ProjectInput(
                    external_id="astana",
                    name="Astana project",
                    city="Астана",
                    district="Есиль",
                    source_url="https://example.com/astana",
                    observed_at=observed,
                    published_starting_price_kzt=30_000_000,
                ),
                ProjectInput(
                    external_id="almaty",
                    name="Almaty project",
                    city="Алматы",
                    district="Медеу",
                    source_url="https://example.com/almaty",
                    observed_at=observed,
                ),
                ProjectInput(
                    external_id="stale",
                    name="Stale project",
                    city="Астана",
                    district="Есиль",
                    source_url="https://example.com/stale",
                    observed_at=observed - timedelta(days=3),
                ),
            ],
            observed,
        )
        session.add(
            Apartment(
                id=apartment_id,
                provider_id="public",
                external_id="actual-lot",
                complex_id="astana",
                complex_name="Astana project",
                city="Астана",
                district="Есиль",
                address="Published address",
                rooms=2,
                area_m2=65,
                floor=4,
                total_floors=9,
                price_kzt=55_000_000,
                status="available",
                source_url="https://example.com/actual-lot",
                observed_at=observed,
            )
        )
    try:
        yield SimpleNamespace(
            db=db,
            settings=settings,
            coordination=coordination,
            projects=projects,
            service=service,
            user=user,
            other=other,
            observed=observed,
            apartment_id=apartment_id,
            astana_id=project_id("public", "astana"),
            almaty_id=project_id("public", "almaty"),
            stale_id=project_id("public", "stale"),
        )
    finally:
        await coordination.close()
        await db.engine.dispose()


async def create(chat, criteria=None, client_id=None, user=None):
    return await chat.service.create(
        user or chat.user,
        ProjectConversationCreate(
            criteria=criteria or ProjectCriteria(), client_conversation_id=client_id
        ),
    )


async def count(chat, model):
    async with chat.db.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_creation_replay_uses_immutable_normalized_initial_criteria(project_chat):
    chat = project_chat
    key = uuid4()
    original = ProjectCriteria(city="astana", districts=["Есиль", "Есиль"])
    conversation = await create(chat, original, key)
    await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(), message="Алматы", criteria=ProjectCriteria(city="Алматы")
        ),
    )
    replay = await create(chat, ProjectCriteria(city="Астана", districts=["Есиль"]), key)
    assert replay.id == conversation.id and replay.criteria.city == "Алматы"
    with pytest.raises(HTTPException) as error:
        await create(chat, ProjectCriteria(city="Алматы"), key)
    assert error.value.status_code == 409 and error.value.detail == "idempotency_conflict"
    assert await count(chat, ProjectConversationRecord) == 1


async def test_concurrent_creation_and_client_id_scope(project_chat):
    chat = project_chat
    key = uuid4()
    body = ProjectConversationCreate(client_conversation_id=key, criteria=ProjectCriteria(city="Астана"))
    rows = await asyncio.gather(*(chat.service.create(chat.user, body) for _ in range(6)))
    assert len({row.id for row in rows}) == 1
    other = await chat.service.create(chat.other, body)
    assert other.id != rows[0].id
    assert await count(chat, ProjectConversationRecord) == 2
    first, second = await create(chat), await create(chat)
    assert first.id != second.id
    with pytest.raises(HTTPException) as error:
        await chat.service.create("guest-without-user", body)
    assert error.value.status_code == 401


async def test_concurrent_creation_near_limit_does_not_exceed_user_cap(project_chat):
    chat = project_chat
    async with chat.db.sessions.begin() as session:
        session.add_all(
            ProjectConversationRecord(
                user_id=chat.user,
                initial_criteria_hash="a" * 64,
                criteria={},
            )
            for _ in range(99)
        )
    outcomes = await asyncio.gather(
        *(create(chat, client_id=uuid4()) for _ in range(6)), return_exceptions=True
    )
    assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
    assert all(
        not isinstance(outcome, BaseException)
        or (isinstance(outcome, HTTPException) and outcome.status_code == 409)
        for outcome in outcomes
    )
    assert await count(chat, ProjectConversationRecord) == 100


async def test_foreign_conversation_is_404_for_all_private_operations(project_chat):
    chat = project_chat
    conversation = await create(chat)
    for operation in (
        chat.service.history(chat.other, conversation.id),
        chat.service.delete(chat.other, conversation.id),
        chat.service.turn(
            chat.other,
            conversation.id,
            ProjectTurnRequest(client_turn_id=uuid4(), message="Астана"),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404 and error.value.detail == "conversation_not_found"
    assert (await chat.service.list(chat.other)).items == []
    assert len((await chat.service.list(chat.user)).items) == 1
    assert await count(chat, ProjectTurnRecord) == 0


async def test_turn_replay_survives_restart_and_catalog_changes(project_chat, monkeypatch):
    chat = project_chat
    conversation = await create(chat)
    request = ProjectTurnRequest(
        client_turn_id=uuid4(), message="Форма поиска", criteria=ProjectCriteria(city="Астана")
    )
    first = await chat.service.turn(chat.user, conversation.id, request)
    async with chat.db.sessions.begin() as session:
        row = await session.get(ProjectRecord, chat.astana_id)
        row.name = "Changed public catalog"

    async def forbidden(*args, **kwargs):
        raise AssertionError("A saved turn replay must not execute catalog work")

    monkeypatch.setattr(chat.projects, "search", forbidden)
    monkeypatch.setattr(chat.projects, "facets", forbidden)
    monkeypatch.setattr(chat.projects, "get", forbidden)
    fresh_coordination = Coordination("")
    restarted = ProjectConversations(chat.db, fresh_coordination, chat.projects, chat.settings)
    replay = await restarted.turn(chat.user, conversation.id, request)
    assert replay.model_dump(mode="json") == first.model_dump(mode="json")
    assert replay.results.items[0].name == "Astana project"
    assert await count(chat, ProjectTurnRecord) == 1
    with pytest.raises(HTTPException) as error:
        await restarted.turn(
            chat.user, conversation.id, request.model_copy(update={"message": "Changed"})
        )
    assert error.value.detail == "idempotency_conflict"
    with pytest.raises(HTTPException) as error:
        await restarted.turn(
            chat.user,
            conversation.id,
            request.model_copy(update={"criteria": ProjectCriteria(city="Алматы")}),
        )
    assert error.value.detail == "idempotency_conflict"
    history = await restarted.history(chat.user, conversation.id)
    assert history.turns[0].response.model_dump(mode="json") == first.model_dump(mode="json")
    await fresh_coordination.close()


async def test_explicit_starting_price_is_not_a_lot_budget(project_chat):
    chat = project_chat
    conversation = await create(chat)
    response = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(),
            message="Покажи ЖК в Астана, район Есиль, начальная цена до 35 млн тенге",
        ),
    )
    assert response.mode == "basic" and response.action == "search"
    assert response.criteria.city == "Астана" and response.criteria.districts == ["Есиль"]
    assert response.criteria.price_mode == "published_starting_price"
    assert response.criteria.price_max == 35_000_000
    assert [row.id for row in response.results.items] == [chat.astana_id]
    project = response.results.items[0]
    assert project.published_starting_price.amount_kzt == 30_000_000
    assert project.observed_listing_minimum.amount_kzt == 55_000_000
    assert {citation.url for citation in response.citations} == {
        "https://example.com/astana",
        "https://example.com/actual-lot",
    }
    assert all(
        citation.observed_at == chat.observed and not citation.demo for citation in response.citations
    )


@pytest.mark.parametrize(
    "message",
    [
        "Астана, бюджет до 35 млн, 2 комнаты",
        "Астана до 35 млн",
        "Костанай",
        "Астана и Алматы",
        "Астана, рядом действующая школа",
        "Астана, этаж не ниже 5",
    ],
)
async def test_unsupported_text_clarifies_without_guessing_or_changing_criteria(project_chat, message):
    chat = project_chat
    initial = ProjectCriteria(city="Алматы", districts=["Медеу"])
    conversation = await create(chat, initial)
    response = await chat.service.turn(
        chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message=message)
    )
    assert response.action == "clarify" and response.results is None
    assert response.criteria == initial and response.unsupported_conditions
    assert (await chat.service.history(chat.user, conversation.id)).criteria == initial


async def test_structured_filters_are_authoritative_and_city_change_clears_old_district(project_chat):
    chat = project_chat
    conversation = await create(
        chat,
        ProjectCriteria(
            city="Алматы",
            districts=["Медеу"],
            bounds={"south": 43, "west": 76, "north": 44, "east": 77},
        ),
    )
    response = await chat.service.turn(
        chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
    )
    assert response.criteria.city == "Астана" and response.criteria.districts == []
    assert response.criteria.bounds is None
    explicit = ProjectCriteria(
        city="Астана", rooms=[2], price_mode="observed_listing_minimum", price_max=60_000_000
    )
    response = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(client_turn_id=uuid4(), message="Любой текст формы", criteria=explicit),
    )
    assert response.action == "search" and response.criteria == explicit
    assert [row.id for row in response.results.items] == [chat.astana_id]
    assert response.results.items[0].display_price.kind == "observed_listing_minimum"
    assert "не означает начальную цену" in response.message
    assert (await chat.service.history(chat.user, conversation.id)).criteria == explicit


async def test_failed_catalog_work_does_not_partially_commit_criteria_or_turn(project_chat, monkeypatch):
    chat = project_chat
    conversation = await create(chat, ProjectCriteria(city="Астана"))

    async def failed(*args, **kwargs):
        raise HTTPException(503, "catalog_unavailable")

    monkeypatch.setattr(chat.projects, "search", failed)
    with pytest.raises(HTTPException) as error:
        await chat.service.turn(
            chat.user,
            conversation.id,
            ProjectTurnRequest(
                client_turn_id=uuid4(), message="Форма", criteria=ProjectCriteria(city="Алматы")
            ),
        )
    assert error.value.status_code == 503
    assert (await chat.service.history(chat.user, conversation.id)).criteria.city == "Астана"
    assert await count(chat, ProjectTurnRecord) == 0
    assert await chat.coordination.get("project-conversation:" + str(conversation.id)) is None


async def test_compare_uses_known_projects_and_preserves_sources_without_winner(project_chat):
    chat = project_chat
    conversation = await create(chat)
    response = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(),
            message="Сравнить",
            action="compare",
            selected_project_ids=[chat.astana_id, chat.almaty_id],
        ),
    )
    assert response.action == "compare" and response.results is None
    assert [row.id for row in response.comparison.projects] == [chat.astana_id, chat.almaty_id]
    assert response.comparison.projects[1].display_price.kind == "unknown"
    assert response.comparison.projects[1].display_price.amount_kzt is None
    assert response.citations and all(
        citation.url.startswith("https://") for citation in response.citations
    )
    assert "winner" not in response.model_dump_json() and "score" not in response.model_dump_json()
    with pytest.raises(HTTPException) as error:
        await chat.service.turn(
            chat.user,
            conversation.id,
            ProjectTurnRequest(
                client_turn_id=uuid4(),
                message="Сравнить",
                action="compare",
                criteria=ProjectCriteria(city="Алматы"),
                selected_project_ids=[chat.astana_id, uuid4()],
            ),
        )
    assert error.value.status_code == 404 and error.value.detail == "project_not_found"
    assert (await chat.service.history(chat.user, conversation.id)).criteria.city is None
    assert await count(chat, ProjectTurnRecord) == 1


async def test_explain_keeps_stale_and_unknown_distinct_from_availability(project_chat):
    chat = project_chat
    conversation = await create(chat)
    response = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(),
            message="Расскажи",
            action="explain",
            selected_project_ids=[chat.stale_id],
        ),
    )
    project = response.results.items[0]
    assert response.mode == "basic" and response.action == "explain"
    assert project.freshness == "stale"
    assert project.published_starting_price is None and project.observed_listing_minimum is None
    assert project.display_price.kind == "unknown" and project.display_price.amount_kzt is None
    assert "не подтверждают текущее наличие" in response.message
    assert response.citations[0].url == "https://example.com/stale"
    assert response.citations[0].observed_at < chat.observed


async def test_explain_uses_batch_summaries_instead_of_full_project_details(project_chat, monkeypatch):
    chat = project_chat
    observed = utcnow() - timedelta(seconds=1)
    async with chat.db.sessions.begin() as session:
        await upsert_public_projects(
            session,
            "public",
            [
                ProjectInput(
                    external_id="astana",
                    name="Astana project",
                    city="Астана",
                    district="Есиль",
                    source_url="https://example.com/astana",
                    observed_at=observed,
                    published_starting_price_kzt=30_000_000,
                    layouts=[
                        {
                            "external_id": str(index),
                            "name": f"Layout {index}",
                            "rooms": 2,
                            "area_m2": 65,
                            "source_url": "https://example.com/layouts",
                            "observed_at": observed,
                        }
                        for index in range(500)
                    ],
                    documents=[
                        {
                            "name": "Public brochure",
                            "url": "https://example.com/brochure.pdf",
                            "source_url": "https://example.com/astana",
                            "observed_at": observed,
                        }
                    ],
                    buildings=[
                        {
                            "external_id": "building",
                            "name": "Published building",
                            "source_url": "https://example.com/astana",
                            "observed_at": observed,
                        }
                    ],
                )
            ],
            observed,
        )
    conversation = await create(chat)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Explain must not load the full project detail")

    batch = chat.projects.get_many
    calls = []

    async def tracked(ids, *, full=False):
        calls.append((ids, full))
        return await batch(ids, full=full)

    monkeypatch.setattr(chat.projects, "get", forbidden)
    monkeypatch.setattr(chat.projects, "get_many", tracked)
    response = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(),
            message="Расскажи",
            action="explain",
            selected_project_ids=[chat.astana_id, chat.almaty_id],
        ),
    )
    assert calls == [([chat.astana_id, chat.almaty_id], False)]
    project = response.results.items[0]
    assert project.available_layout_count == 500
    assert project.layouts == project.documents == project.buildings == []
    assert project.published_starting_price.amount_kzt == 30_000_000
    assert {citation.url for citation in response.citations} == {
        "https://example.com/astana",
        "https://example.com/almaty",
        "https://example.com/actual-lot",
    }


async def test_rich_history_is_compact_and_saved_turn_replay_stays_exact(project_chat, monkeypatch):
    chat = project_chat
    conversation = await create(chat)
    request = ProjectTurnRequest(
        client_turn_id=uuid4(),
        message="Original rich snapshot",
        action="explain",
        selected_project_ids=[chat.astana_id, chat.almaty_id, chat.stale_id],
    )
    first = await chat.service.turn(chat.user, conversation.id, request)
    rich = first.model_dump(mode="json")
    for project in rich["results"]["items"]:
        project["available_layout_count"] = 500
        project["layouts"] = [
            {
                "id": str(uuid4()),
                "project_id": project["id"],
                "external_id": str(index),
                "name": f"Layout {index} " + "L" * 150,
                "rooms": 2,
                "area_m2": 65,
                "source_url": project["source_url"],
                "observed_at": project["observed_at"],
                "received_at": project["received_at"],
                "provenance": project["provenance"],
                "freshness": project["freshness"],
            }
            for index in range(500)
        ]
        project["documents"] = [
            {
                "name": f"Document {index} " + "D" * 150,
                "url": f"https://example.com/document-{index}.pdf",
                "kind": "brochure",
                "source_url": project["source_url"],
                "observed_at": project["observed_at"],
            }
            for index in range(50)
        ]
        project["buildings"] = [
            {
                "external_id": str(index),
                "name": f"Building {index} " + "B" * 150,
                "source_url": project["source_url"],
                "observed_at": project["observed_at"],
            }
            for index in range(100)
        ]
        project["images"] = [
            {
                "url": f"https://example.com/image-{index}.jpg",
                "kind": "facade",
                "caption": "I" * 250,
                "source_url": project["source_url"],
                "observed_at": project["observed_at"],
            }
            for index in range(100)
        ]
    # Represents rich snapshots saved by an earlier development build. The first
    # actual request retains its original durable idempotency hash for replay.
    rich = ProjectTurnResponse.model_validate(rich).model_dump(mode="json")
    stored_bytes = len(json.dumps(rich, ensure_ascii=False).encode())
    assert stored_bytes > 1_000_000
    async with chat.db.sessions.begin() as session:
        original = await session.get(ProjectTurnRecord, str(first.turn_id))
        original.response = rich
        for index in range(29):
            turn_id, client_id = str(uuid4()), str(uuid4())
            payload = copy.copy(rich)
            payload.update(turn_id=turn_id, client_turn_id=client_id)
            session.add(
                ProjectTurnRecord(
                    id=turn_id,
                    conversation_id=str(conversation.id),
                    client_turn_id=client_id,
                    message=f"Earlier rich snapshot {index}",
                    request_hash="a" * 64,
                    response=payload,
                    state="complete",
                    created_at=first.created_at,
                )
            )
    clarify = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(client_turn_id=uuid4(), message="Бюджет квартиры до 35 млн"),
    )
    assert clarify.action == "clarify"

    async def forbidden(*args, **kwargs):
        raise AssertionError("History and durable replay must use saved data only")

    monkeypatch.setattr(chat.projects, "get", forbidden)
    monkeypatch.setattr(chat.projects, "get_many", forbidden)
    monkeypatch.setattr(chat.projects, "search", forbidden)
    monkeypatch.setattr(chat.projects, "compare", forbidden)
    history = await chat.service.history(chat.user, conversation.id)
    assert len(history.turns) == 30 and history.has_more
    assert history.turns[-1].response.action == "clarify"
    snapshots = [turn.response for turn in history.turns if turn.response and turn.response.results]
    assert len(snapshots) == 1
    latest = snapshots[0]
    assert len(latest.results.items) == 3
    assert all(
        project.layouts == project.documents == project.buildings == []
        and len(project.images) == 1
        and project.available_layout_count == 500
        for project in latest.results.items
    )
    for turn in history.turns:
        assert turn.response.criteria == first.criteria
        if turn.response.action != "clarify":
            assert turn.response.citations == first.citations
        if turn.response is not latest:
            assert turn.response.results is None and turn.response.comparison is None
    assert len(history.model_dump_json().encode()) < 150_000
    replay = await chat.service.turn(chat.user, conversation.id, request)
    assert replay.model_dump(mode="json") == rich
    assert len(replay.results.items[0].layouts) == 500
    async with chat.db.sessions() as session:
        saved = await session.get(ProjectTurnRecord, str(first.turn_id))
        assert saved.response == rich


async def test_history_restores_only_latest_comparison_after_clarification(project_chat):
    chat = project_chat
    conversation = await create(chat)
    search_request = ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
    search = await chat.service.turn(chat.user, conversation.id, search_request)
    comparison = await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(
            client_turn_id=uuid4(),
            message="Сравнить",
            action="compare",
            selected_project_ids=[chat.astana_id, chat.almaty_id],
        ),
    )
    await chat.service.turn(
        chat.user,
        conversation.id,
        ProjectTurnRequest(client_turn_id=uuid4(), message="Этаж не ниже 5"),
    )
    history = await chat.service.history(chat.user, conversation.id)
    assert len(history.turns) == 3
    assert history.turns[0].response.results is None
    assert history.turns[0].response.citations == search.citations
    assert history.turns[1].response.comparison == comparison.comparison
    assert history.turns[2].response.action == "clarify"
    assert history.turns[2].response.results is None
    assert history.turns[2].response.comparison is None
    replay = await chat.service.turn(chat.user, conversation.id, search_request)
    assert replay == search and replay.results is not None


async def test_delete_cascades_turns_and_preserves_all_favorites_and_legacy_chat(project_chat):
    chat = project_chat
    conversation = await create(chat)
    await chat.service.turn(
        chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
    )
    async with chat.db.sessions.begin() as session:
        session.add(ProjectFavorite(user_id=chat.user, project_id=chat.astana_id))
        session.add(Favorite(user_id=chat.user, apartment_id=chat.apartment_id))
        session.add(Conversation(user_id=chat.user, preferences={"city": "Астана"}))
    await chat.service.delete(chat.user, conversation.id)
    assert await count(chat, ProjectConversationRecord) == 0
    assert await count(chat, ProjectTurnRecord) == 0
    assert await count(chat, ProjectFavorite) == 1
    assert await count(chat, Favorite) == 1
    assert await count(chat, Conversation) == 1
    with pytest.raises(HTTPException) as error:
        await chat.service.history(chat.user, conversation.id)
    assert error.value.status_code == 404


async def test_concurrent_turn_has_one_execution_and_blocks_delete(project_chat, monkeypatch):
    chat = project_chat
    conversation = await create(chat)
    request = ProjectTurnRequest(
        client_turn_id=uuid4(), message="Форма", criteria=ProjectCriteria(city="Астана")
    )
    started, finish = asyncio.Event(), asyncio.Event()
    search = chat.projects.search
    calls = 0

    async def delayed(body):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return await search(body)

    monkeypatch.setattr(chat.projects, "search", delayed)
    pending = asyncio.create_task(chat.service.turn(chat.user, conversation.id, request))
    await asyncio.wait_for(started.wait(), timeout=5)
    try:
        with pytest.raises(HTTPException) as error:
            await chat.service.turn(chat.user, conversation.id, request)
        assert error.value.status_code == 409 and error.value.detail == "turn_in_progress"
        with pytest.raises(HTTPException) as error:
            await chat.service.delete(chat.user, conversation.id)
        assert error.value.detail == "conversation_busy"
    finally:
        finish.set()
    first = await pending
    replay = await chat.service.turn(chat.user, conversation.id, request)
    assert replay == first and calls == 1
    assert await count(chat, ProjectTurnRecord) == 1
    assert await chat.coordination.get("project-conversation:" + str(conversation.id)) is None


async def test_delete_between_preflight_and_acquire_is_rechecked(project_chat, monkeypatch):
    chat = project_chat
    conversation = await create(chat)
    acquire = chat.coordination.acquire

    async def deleted(key, ttl):
        async with chat.db.sessions.begin() as session:
            await session.execute(
                delete(ProjectConversationRecord).where(
                    ProjectConversationRecord.id == str(conversation.id)
                )
            )
        return await acquire(key, ttl)

    monkeypatch.setattr(chat.coordination, "acquire", deleted)
    with pytest.raises(HTTPException) as error:
        await chat.service.turn(
            chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
        )
    assert error.value.status_code == 404
    assert await count(chat, ProjectTurnRecord) == 0
    assert await chat.coordination.get("project-conversation:" + str(conversation.id)) is None


async def test_history_preserves_tied_timestamps_and_bounds_turn_count(project_chat):
    chat = project_chat
    conversation = await create(chat)
    timestamp = utcnow()
    turn_ids = sorted(str(uuid4()) for _ in range(200))
    async with chat.db.sessions.begin() as session:
        session.add_all(
            ProjectTurnRecord(
                id=turn_id,
                conversation_id=str(conversation.id),
                client_turn_id=str(uuid4()),
                message=str(index),
                request_hash="a" * 64,
                response={},
                state="complete",
                created_at=timestamp,
            )
            for index, turn_id in enumerate(turn_ids)
        )
    all_ids, before = [], None
    while True:
        history = await chat.service.history(chat.user, conversation.id, before=before)
        assert len(history.turns) <= 30
        all_ids = [str(turn.id) for turn in history.turns] + all_ids
        if not history.has_more:
            assert history.next_before is None
            break
        before = history.next_before
    assert all_ids == turn_ids
    with pytest.raises(HTTPException) as error:
        await chat.service.turn(
            chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
        )
    assert error.value.status_code == 409 and error.value.detail == "conversation_limit"
    assert await count(chat, ProjectTurnRecord) == 200


async def test_history_cursor_cannot_read_a_foreign_conversations_anchor(project_chat):
    chat = project_chat
    own = await create(chat)
    other = await create(chat, user=chat.other)
    other_turn = await chat.service.turn(
        chat.other, other.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
    )
    with pytest.raises(HTTPException) as error:
        await chat.service.history(chat.user, own.id, before=other_turn.turn_id)
    assert error.value.status_code == 404 and error.value.detail == "turn_not_found"


async def test_profile_deletion_cascades_new_conversations_and_turns(project_chat):
    chat = project_chat
    conversation = await create(chat)
    await chat.service.turn(
        chat.user, conversation.id, ProjectTurnRequest(client_turn_id=uuid4(), message="Астана")
    )
    async with chat.db.sessions.begin() as session:
        await session.execute(delete(User).where(User.id == chat.user))
    assert await count(chat, ProjectConversationRecord) == 0
    assert await count(chat, ProjectTurnRecord) == 0
