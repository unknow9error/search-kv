"""ASGI/real DB contract checks with controlled sources, never live business data."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.domain.models import utcnow
from app.domain.project_input import ProjectInput
from app.domain.project_models import (
    DataReportRecord,
    ProjectConversationRecord,
    ProjectFavorite,
    ProjectRecord,
    ProjectTurnRecord,
)
from app.domain.schemas import ListingInput, Snapshot
from app.services.projects import project_id


@pytest.fixture
async def projects_seeded(app):
    now = utcnow()
    source = "https://example.com/public-projects"
    alpha = ProjectInput(
        external_id="alpha",
        name="Project Alpha",
        city="Астана",
        district="Есиль",
        developer_name="Developer A",
        latitude=51.12,
        longitude=71.41,
        source_url=source,
        observed_at=now,
        published_starting_price_kzt=29_900_000,
        stage="under_construction",
        completion="IV кв. 2027",
        finish="Предчистовая",
        website_url="https://example.com/alpha",
        website_scope="project",
        buildings=[
            {
                "external_id": "b1",
                "name": "Корпус 1",
                "completion": "IV кв. 2027",
                "source_url": source,
                "observed_at": now,
            }
        ],
        images=[
            {
                "url": "https://example.com/facade.png",
                "kind": "facade",
                "source_url": source,
                "observed_at": now,
            }
        ],
        documents=[
            {
                "name": "Опубликованный документ",
                "url": "https://example.com/doc.pdf",
                "source_url": source,
                "observed_at": now,
            }
        ],
        layouts=[
            {
                "external_id": "type-2",
                "name": "Тип 2",
                "rooms": 2,
                "area_m2": 62.4,
                "image_url": "https://example.com/plan.png",
                "source_url": source,
                "observed_at": now,
            }
        ],
    )
    beta = ProjectInput(
        external_id="beta", name="Project Beta", city="Астана", source_url=source, observed_at=now
    )
    gamma = ProjectInput(
        external_id="gamma",
        name="Project Gamma",
        city="Астана",
        developer_name="Developer B",
        latitude=51.7,
        longitude=71.9,
        source_url=source,
        observed_at=now,
        published_starting_price_kzt=31_500_000,
    )
    lot = ListingInput(
        external_id="lot-1",
        complex_id="alpha",
        complex_name="Project Alpha",
        city="Астана",
        address="Адрес корпуса",
        rooms=2,
        area_m2=62.4,
        floor=5,
        total_floors=12,
        price_kzt=42_000_000,
        status="available",
        source_url="https://example.com/lot-1",
        image_url="https://example.com/plan.png",
    )
    await app.state.catalog.ingest(
        "demo-garden", Snapshot(as_of=now, complete=False, items=[lot], projects=[alpha, beta])
    )
    await app.state.catalog.ingest(
        "demo-river", Snapshot(as_of=now, complete=False, items=[], projects=[gamma])
    )
    return {
        "alpha": project_id("demo-garden", "alpha"),
        "beta": project_id("demo-garden", "beta"),
        "gamma": project_id("demo-river", "gamma"),
    }


async def test_catalog_map_details_layouts_comparison_and_sources(client, projects_seeded):
    ids = projects_seeded
    configuration = (await client.get("/v1/config")).json()
    assert {"project_catalog", "project_layouts", "project_conversations_basic"} <= set(
        configuration["capabilities"]
    )
    page = await client.post("/v1/projects/search", json={"criteria": {"city": "astana"}})
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert all(not row["layouts"] and not row["documents"] for row in page.json()["items"])
    by_price = (
        await client.post(
            "/v1/projects/search",
            json={
                "criteria": {
                    "city": "Астана",
                    "price_mode": "published_starting_price",
                    "price_max": 35_000_000,
                }
            },
        )
    ).json()
    assert {item["id"] for item in by_price["items"]} == {ids["alpha"], ids["gamma"]}
    observed = (
        await client.post(
            "/v1/projects/search",
            json={
                "criteria": {
                    "price_mode": "observed_listing_minimum",
                    "price_max": 35_000_000,
                    "rooms": [2],
                }
            },
        )
    ).json()
    assert observed["total"] == 0  # Public project 'from' is not a cheap exact lot.
    mapped = (
        await client.post(
            "/v1/projects/map",
            json={
                "criteria": {
                    "city": "Астана",
                    "bounds": {"south": 51.0, "west": 71.0, "north": 51.3, "east": 71.6},
                }
            },
        )
    ).json()
    assert [row["id"] for row in mapped["items"]] == [ids["alpha"]]
    assert mapped["unknown_coordinates_count"] == 1
    detail = (await client.get(f"/v1/projects/{ids['alpha']}")).json()
    assert detail["published_starting_price"]["amount_kzt"] == 29_900_000
    assert detail["observed_listing_minimum"]["amount_kzt"] == 42_000_000
    assert detail["images"][0]["kind"] == "facade"
    assert detail["website_scope"] == "project"
    layout = (await client.get(f"/v1/projects/{ids['alpha']}/layouts")).json()["items"][0]
    standalone = (await client.get("/v1/project-layouts/" + layout["id"])).json()
    assert standalone["kind"] == "layout_type"
    assert not {"price_kzt", "floor", "status"} & standalone.keys()
    lots = (await client.get(f"/v1/projects/{ids['alpha']}/apartments")).json()
    assert lots["total"] == 1 and lots["items"][0]["price_kzt"] == 42_000_000
    comparison = await client.post(
        "/v1/projects/compare", json={"project_ids": [ids["alpha"], ids["beta"]]}
    )
    assert comparison.status_code == 200
    assert not {"winner", "score"} & comparison.json().keys()
    sources = (await client.get("/v1/catalog/sources")).json()
    assert sum(row["project_count"] for row in sources["items"]) == 3
    assert all(row["provenance"] == "demo" for row in sources["items"])


async def test_cursor_map_and_input_errors_are_stable(client, projects_seeded):
    request = {"criteria": {"city": "Астана"}, "limit": 1, "sort": "name"}
    first = (await client.post("/v1/projects/search", json=request)).json()
    second = (
        await client.post("/v1/projects/search", json={**request, "cursor": first["next_cursor"]})
    ).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]
    changed = await client.post(
        "/v1/projects/search",
        json={**request, "criteria": {"city": "Алматы"}, "cursor": first["next_cursor"]},
    )
    assert changed.status_code == 422 and changed.json() == {"error": {"code": "invalid_cursor"}}
    for body in [
        {"criteria": {"price_mode": "unknown"}},
        {"criteria": {"bounds": {"south": 52, "north": 51, "west": 71, "east": 72}}},
        {"criteria": {"booking": True}},
    ]:
        invalid = await client.post("/v1/projects/search", json=body)
        assert invalid.status_code == 422 and invalid.json() == {"error": {"code": "invalid_request"}}
    malformed = await client.get("/v1/projects/not-a-uuid")
    assert malformed.status_code == 422


async def test_personal_favorites_recovery_and_profile_cascade(client, app, projects_seeded):
    identifier = projects_seeded["alpha"]
    assert (await client.put(f"/v1/projects/favorites/{identifier}")).status_code == 204
    assert (await client.put(f"/v1/projects/favorites/{identifier}")).status_code == 204
    assert len((await client.get("/v1/projects/favorites")).json()["items"]) == 1
    report_body = {
        "client_report_id": str(uuid4()),
        "project_id": identifier,
        "category": "price",
        "message": "В источнике другая опубликованная цена",
    }
    initial = await client.post("/v1/data-reports", json=report_body)
    assert initial.status_code == 201
    replay = await client.post("/v1/data-reports", json=report_body)
    assert replay.status_code == 201 and replay.json() == initial.json()
    conflict = await client.post("/v1/data-reports", json={**report_body, "message": "Другой запрос"})
    assert conflict.status_code == 409
    owner_header = client.headers["Authorization"]
    other = (await client.post("/v1/auth/anonymous")).json()
    client.headers["Authorization"] = "Bearer " + other["access_token"]
    assert (await client.get("/v1/projects/favorites")).json()["items"] == []
    assert (await client.get("/v1/data-reports")).json()["items"] == []
    client.headers["Authorization"] = owner_header
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    restored = (await client.post("/v1/auth/recover", json={"recovery_code": code})).json()
    client.headers["Authorization"] = "Bearer " + restored["access_token"]
    assert len((await client.get("/v1/projects/favorites")).json()["items"]) == 1
    conversation = (await client.post("/v1/projects/conversations", json={})).json()
    response = await client.post(
        f"/v1/projects/conversations/{conversation['id']}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Ищу ЖК", "criteria": {"city": "Астана"}},
    )
    assert response.status_code == 200
    assert (await client.delete("/v1/me")).status_code == 204
    async with app.state.db.sessions() as db:
        for table in (ProjectFavorite, ProjectConversationRecord, ProjectTurnRecord, DataReportRecord):
            assert await db.scalar(select(func.count()).select_from(table)) == 0
        assert await db.scalar(select(func.count()).select_from(ProjectRecord)) == 3


async def test_project_conversation_api_durable_replay_and_ownership(client, projects_seeded):
    creation = {"client_conversation_id": str(uuid4()), "criteria": {"city": "Астана"}}
    created = await client.post("/v1/projects/conversations", json=creation)
    assert created.status_code == 201
    identifier = created.json()["id"]
    assert (await client.post("/v1/projects/conversations", json=creation)).json()["id"] == identifier
    body = {
        "client_turn_id": str(uuid4()),
        "message": "Сравнить выбранные ЖК",
        "action": "compare",
        "selected_project_ids": [projects_seeded["alpha"], projects_seeded["beta"]],
    }
    first = await client.post(f"/v1/projects/conversations/{identifier}/turns", json=body)
    assert first.status_code == 200 and first.json()["mode"] == "basic"
    assert first.json()["comparison"]
    assert (
        await client.post(f"/v1/projects/conversations/{identifier}/turns", json=body)
    ).json() == first.json()
    assert (
        await client.post(
            f"/v1/projects/conversations/{identifier}/turns", json={**body, "message": "Other"}
        )
    ).status_code == 409
    history = (await client.get(f"/v1/projects/conversations/{identifier}")).json()
    assert len(history["turns"]) == 1 and history["turns"][0]["response"]["mode"] == "basic"
    owner = client.headers["Authorization"]
    token = (await client.post("/v1/auth/anonymous")).json()["access_token"]
    client.headers["Authorization"] = "Bearer " + token
    assert (await client.get(f"/v1/projects/conversations/{identifier}")).status_code == 404
    assert (await client.delete(f"/v1/projects/conversations/{identifier}")).status_code == 404
    client.headers["Authorization"] = owner
    assert (await client.delete(f"/v1/projects/conversations/{identifier}")).status_code == 204


async def test_catalog_reads_and_project_chat_do_not_call_source_or_model(
    client, app, projects_seeded, monkeypatch
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Database-only route invoked a source/model")

    for provider in app.state.catalog.providers.values():
        monkeypatch.setattr(provider, "fetch", forbidden)
        monkeypatch.setattr(provider, "verify", forbidden)
    monkeypatch.setattr(app.state.assistant, "plan", forbidden)
    for path in (
        "/v1/projects",
        "/v1/projects/facets",
        "/v1/catalog/sources",
        "/v1/projects/favorites",
        f"/v1/projects/{projects_seeded['alpha']}",
        f"/v1/projects/{projects_seeded['alpha']}/layouts",
    ):
        assert (await client.get(path)).status_code == 200
    assert (await client.post("/v1/projects/search", json={})).status_code == 200
    assert (await client.post("/v1/projects/map", json={})).status_code == 200
    created = (await client.post("/v1/projects/conversations", json={})).json()
    body = {"client_turn_id": str(uuid4()), "message": "Ищу ЖК", "criteria": {"city": "Астана"}}
    assert (
        await client.post(f"/v1/projects/conversations/{created['id']}/turns", json=body)
    ).status_code == 200


async def test_refresh_remains_source_observation_not_availability(client, projects_seeded):
    response = await client.post(f"/v1/projects/{projects_seeded['alpha']}/refresh")
    assert response.status_code == 200
    assert response.json()["availability_confirmed"] is False


async def test_concurrent_report_and_favorite_put_are_idempotent(client, app, projects_seeded):
    identifier = projects_seeded["alpha"]
    body = {
        "client_report_id": str(uuid4()),
        "project_id": identifier,
        "category": "other",
        "message": "Повтор одного сообщения",
    }
    reports = await asyncio.gather(*[client.post("/v1/data-reports", json=body) for _ in range(4)])
    successful = [response.json()["id"] for response in reports if response.status_code == 201]
    assert successful and len(set(successful)) == 1
    assert all(response.status_code in (201, 409) for response in reports)
    put = await asyncio.gather(*[client.put(f"/v1/projects/favorites/{identifier}") for _ in range(4)])
    assert all(response.status_code in (204, 409) for response in put)
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DataReportRecord)) == 1
        assert await db.scalar(select(func.count()).select_from(ProjectFavorite)) == 1


async def test_stale_favorite_is_readable_and_not_sold(client, app, projects_seeded):
    identifier = projects_seeded["alpha"]
    await client.put(f"/v1/projects/favorites/{identifier}")
    async with app.state.db.sessions.begin() as db:
        row = await db.get(ProjectRecord, identifier)
        row.observed_at = utcnow() - timedelta(days=10)
    favorite = (await client.get("/v1/projects/favorites")).json()["items"][0]
    assert favorite["freshness"] == "stale"
    assert favorite["stage"] == "under_construction"


async def test_new_routes_require_identity_and_reject_design_business_actions(client):
    client.headers.pop("Authorization")
    assert (await client.get("/v1/projects")).status_code == 401
    assert (await client.post("/v1/projects/search", json={})).status_code == 401
    assert (await client.post("/v1/projects/book", json={})).status_code in (404, 405)
