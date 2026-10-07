"""SQL-level evidence for the public project API, using isolated synthetic fixtures."""

import base64
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.core.coordination import Coordination
from app.core.db import Database
from app.domain.models import Apartment, Place, ProjectFact, Provider, utcnow
from app.domain.project_models import ProjectRecord
from app.domain.project_schemas import ProjectCriteria, ProjectInput, ProjectSearchRequest
from app.domain.schemas import ListingInput
from app.providers.base import SourceError
from app.services.catalog import Catalog, listing_id
from app.services.projects import (
    Projects,
    bootstrap_legacy_projects,
    layout_id,
    project_id,
    project_projection_from_lots,
    upsert_public_projects,
)


@pytest.fixture
async def projects(tmp_path):
    database_url = (
        os.environ.get("MEKEN_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/projects.sqlite3"
    )
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=database_url,
        fresh_seconds=300,
        max_catalog_age_seconds=86400,
    )
    db = Database(settings.database_url)
    schema = "projects_" + uuid4().hex if database_url.startswith("postgresql") else None
    if schema:
        async with db.engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        db.engine = db.engine.execution_options(schema_translate_map={None: schema})
        db.sessions.configure(bind=db.engine)
    await db.create_schema()
    coordination = Coordination("")
    sources = [
        SimpleNamespace(id=key, name=key, demo=demo, public_data=public, cities=["Астана"])
        for key, demo, public in (
            ("public-a", False, True),
            ("public-b", False, True),
            ("partner", False, False),
            ("demo-a", True, False),
        )
    ]
    catalog = Catalog(db, coordination, sources, settings)
    async with db.sessions.begin() as session:
        session.add_all(
            [
                Provider(id=source.id, name=source.name, demo=source.demo, enabled=True)
                for source in sources
            ]
        )
    service = Projects(db, coordination, catalog, settings)
    try:
        yield service
    finally:
        await coordination.close()
        if schema:
            async with db.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
        await db.engine.dispose()


def _input(external_id="complex", now=None, **values):
    return ProjectInput(
        external_id=external_id,
        name=values.pop("name", "ЖК Публичный"),
        city="Астана",
        source_url="https://example.org/projects/" + external_id,
        observed_at=now or utcnow(),
        **values,
    )


async def _public(service, items, provider_id="public-a", as_of=None):
    async with service.db.sessions.begin() as session:
        return await upsert_public_projects(session, provider_id, items, as_of or utcnow())


def _lot(external_id, complex_id="complex", **values):
    return ListingInput(
        external_id=external_id,
        complex_id=complex_id,
        complex_name="ЖК Публичный",
        city="Астана",
        district="Нұра",
        address="Публичный адрес",
        rooms=values.pop("rooms", 1),
        area_m2=values.pop("area_m2", 40),
        floor=values.pop("floor", 3),
        total_floors=20,
        price_kzt=values.pop("price_kzt", 20_000_000),
        status=values.pop("status", "available"),
        source_url="https://example.org/apartments/" + external_id,
        **values,
    )


async def _lots(service, items, provider_id="public-a", observed=None):
    now = observed or utcnow()
    async with service.db.sessions.begin() as session:
        for item in items:
            payload = item.model_dump(mode="json", exclude={"amenities"})
            session.add(
                Apartment(
                    id=listing_id(provider_id, item.external_id),
                    provider_id=provider_id,
                    observed_at=now,
                    received_at=now,
                    **payload,
                )
            )
        await session.flush()
        await project_projection_from_lots(session, provider_id, items, now)


async def test_independent_public_project_and_provider_identity(projects):
    now = utcnow()
    item = _input(now=now)
    await _public(projects, [item])
    await _public(projects, [item], "public-b")
    results = await projects.search(ProjectSearchRequest())
    assert results.total == 2
    assert {item.id for item in results.items} == {
        project_id("public-a", "complex"),
        project_id("public-b", "complex"),
    }
    for item in results.items:
        assert item.record_origin == "public_project"
        assert item.display_price.kind == "unknown"
        assert item.display_price.amount_kzt is None
        assert item.published_lot_count == 0
        assert item.developer_name is None
        assert item.images == []
        assert item.stage is None


async def test_lot_projection_keeps_unknown_identity_singletons_and_no_facade(projects):
    items = [
        _lot("first", None, image_url="https://example.org/layout.png"),
        _lot("second", None),
        _lot("third", "group"),
        _lot("fourth", "group"),
    ]
    await _lots(projects, items)
    results = await projects.search(ProjectSearchRequest())
    assert results.total == 3
    assert {item.external_id for item in results.items} == {"listing:first", "listing:second", "group"}
    grouped = next(item for item in results.items if item.external_id == "group")
    assert grouped.published_lot_count == 2
    assert all(
        item.record_origin == "observed_lots" and not item.images and not item.layouts
        for item in results.items
    )
    assert all(
        item.published_starting_price is None and item.display_price.kind == "unknown"
        for item in results.items
    )


async def test_project_import_replay_old_batch_and_same_date_conflict_are_atomic(projects):
    now = utcnow()
    original = _input(now=now, published_starting_price_kzt=25_000_000)
    assert await _public(projects, [original], as_of=now) == 1
    first = await projects.get(project_id("public-a", "complex"))
    assert await _public(projects, [original], as_of=now + timedelta(seconds=1)) == 0
    replay = await projects.get(first.id)
    assert replay.version == first.version
    assert replay.observed_at == first.observed_at
    assert replay.received_at == first.received_at
    older = original.model_copy(
        update={"observed_at": now - timedelta(days=1), "published_starting_price_kzt": 10_000_000}
    )
    assert await _public(projects, [older]) == 0
    conflicting = original.model_copy(update={"published_starting_price_kzt": 50_000_000})
    with pytest.raises(SourceError, match="project_observation_conflict"):
        await _public(projects, [_input("would-create"), conflicting])
    assert await projects.get(project_id("public-a", "would-create")) is None
    assert (await projects.get(first.id)).published_starting_price.amount_kzt == 25_000_000


async def test_price_modes_unknowns_and_same_lot_filtering(projects):
    await _public(projects, [_input(published_starting_price_kzt=35_000_000), _input("unknown")])
    await _lots(
        projects,
        [
            _lot("cheap-studio", rooms=1, floor=5, price_kzt=15_000_000),
            _lot("large-two", rooms=2, area_m2=85, floor=2, price_kzt=45_000_000),
            _lot("small-two", rooms=2, area_m2=55, floor=12, price_kzt=30_000_000),
        ],
    )
    unfiltered = await projects.search(ProjectSearchRequest(sort="price_asc"))
    complex_ = unfiltered.items[0]
    assert complex_.published_starting_price.amount_kzt == 35_000_000
    assert complex_.observed_listing_minimum.amount_kzt == 15_000_000
    assert complex_.observed_listing_minimum.source_url.endswith("cheap-studio")
    assert complex_.display_price.kind == "published_starting_price"
    assert unfiltered.items[1].display_price.kind == "unknown"
    assert (
        await projects.search(ProjectSearchRequest(criteria=ProjectCriteria(price_max=34_000_000)))
    ).total == 0
    assert (
        await projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(price_mode="observed_listing_minimum", price_max=20_000_000)
            )
        )
    ).total == 1
    assert (
        await projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(
                    price_mode="observed_listing_minimum", rooms=[2], price_max=20_000_000
                )
            )
        )
    ).total == 0
    assert (
        await projects.search(
            ProjectSearchRequest(criteria=ProjectCriteria(rooms=[2], area_min=80, floor_min=10))
        )
    ).total == 0
    assert (
        await projects.search(
            ProjectSearchRequest(criteria=ProjectCriteria(rooms=[2], area_min=50, floor_min=10))
        )
    ).total == 1


async def test_stale_sold_and_unknown_lots_do_not_establish_price_or_filter(projects):
    await _public(projects, [_input()])
    await _lots(
        projects, [_lot("old-cheap", price_kzt=10_000_000)], observed=utcnow() - timedelta(days=2)
    )
    await _lots(
        projects,
        [
            _lot("sold-cheap", status="sold", price_kzt=11_000_000),
            _lot("unknown-cheap", status="unknown", price_kzt=12_000_000),
            _lot("fresh", rooms=2, price_kzt=40_000_000),
        ],
    )
    result = await projects.get(project_id("public-a", "complex"))
    assert result.observed_listing_minimum.amount_kzt == 40_000_000
    assert result.published_lot_count == 4
    assert result.matched_lot_count == 1
    assert (await projects.search(ProjectSearchRequest(criteria=ProjectCriteria(rooms=[1])))).total == 0
    apartment_page = await projects.apartments(result.id)
    assert apartment_page.total == 1 and apartment_page.items[0].rooms == 2


async def test_public_metadata_never_overwritten_by_lot_projection(projects):
    now = utcnow()
    original = _input(
        now=now - timedelta(hours=2),
        name="Опубликованное имя",
        published_starting_price_kzt=55_000_000,
        completion="II квартал 2020",
        completion_date="2020-06-30",
    )
    await _public(projects, [original])
    await _lots(projects, [_lot("fresh")])
    result = await projects.get(project_id("public-a", "complex"))
    assert result.name == "Опубликованное имя"
    assert result.observed_at == original.observed_at
    assert result.stage is None  # A past published date does not prove completion.
    assert result.published_starting_price.amount_kzt == 55_000_000


async def test_sql_pagination_sort_ties_unknowns_and_complete_cursor_binding(projects):
    now = utcnow()
    await _public(
        projects,
        [
            _input(
                str(index),
                now=now,
                name="Same",
                published_starting_price_kzt=10_000_000 if index < 4 else None,
            )
            for index in range(7)
        ],
    )
    for sort in ("price_asc", "price_desc", "name", "observed_desc"):
        seen, cursor = [], None
        while True:
            page = await projects.search(ProjectSearchRequest(limit=2, sort=sort, cursor=cursor))
            assert page.total == 7 and len(page.items) <= 2
            seen.extend(item.id for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == 7
        if sort in {"price_asc", "price_desc"}:
            assert set(seen[:4]) == {project_id("public-a", str(index)) for index in range(4)}
    first = await projects.search(ProjectSearchRequest(limit=2, sort="name"))
    for changed in (
        ProjectSearchRequest(limit=2, sort="price_asc", cursor=first.next_cursor),
        ProjectSearchRequest(
            limit=2, sort="name", cursor=first.next_cursor, criteria=ProjectCriteria(rooms=[2])
        ),
        ProjectSearchRequest(limit=2, sort="name", cursor="not-json"),
        ProjectSearchRequest(limit=2, sort="name", cursor=base64.urlsafe_b64encode(b"[]").decode()),
    ):
        with pytest.raises(ValueError, match="invalid_cursor"):
            await projects.search(changed)


async def test_map_uses_same_search_filters_and_pages_only_mappable_records(projects):
    await _public(
        projects,
        [
            _input("inside", latitude=51.12, longitude=71.42, developer_name="Published developer"),
            _input("outside", latitude=43.2, longitude=76.8),
            _input("unmapped"),
        ],
    )
    criteria = ProjectCriteria(bounds={"south": 51, "north": 52, "west": 71, "east": 72})
    search = await projects.search(ProjectSearchRequest(criteria=criteria))
    mapped = await projects.map(ProjectSearchRequest(criteria=criteria, limit=1))
    assert search.total == mapped.total == 1
    assert mapped.items[0].external_id == "inside"
    assert search.unknown_coordinates_count == mapped.unknown_coordinates_count == 1
    mapped_all = await projects.map(ProjectSearchRequest(limit=1, sort="name"))
    assert mapped_all.total == 2 and mapped_all.next_cursor
    next_page = await projects.map(
        ProjectSearchRequest(limit=1, sort="name", cursor=mapped_all.next_cursor)
    )
    assert next_page.total == 2 and next_page.items[0].id != mapped_all.items[0].id
    with pytest.raises(ValueError, match="invalid_cursor"):
        await projects.search(ProjectSearchRequest(limit=1, sort="name", cursor=mapped_all.next_cursor))
    facets = await projects.facets()
    assert [(row.value, row.count) for row in facets.developer_names] == [("Published developer", 1)]
    assert facets.price_modes == []
    assert facets.unknown_published_price_count == facets.unknown_observed_price_count == 3


async def test_guard_every_public_read_path_and_stale_saved_details(projects):
    now = utcnow()
    layout = {
        "external_id": "plan",
        "name": "План 2-комнатный",
        "rooms": 2,
        "source_url": "https://example.org/layout",
        "observed_at": now,
    }
    for provider_id in ("public-a", "public-b", "partner", "demo-a"):
        await _public(projects, [_input(now=now, layouts=[layout])], provider_id)
    visible = project_id("public-a", "complex")
    assert (await projects.search(ProjectSearchRequest())).total == 2
    for hidden in ("partner", "demo-a"):
        record_id = project_id(hidden, "complex")
        assert await projects.get(record_id) is None
        assert await projects.layouts(record_id) == []
        assert await projects.layout(layout_id(hidden, "complex", "plan")) is None
        assert await projects.apartments(record_id) is None
        with pytest.raises(ValueError, match="project_not_found"):
            await projects.compare([visible, record_id])
    assert {row.provider_id for row in (await projects.sources()).items} == {"public-a", "public-b"}
    async with projects.db.sessions.begin() as session:
        record = await session.get(ProjectRecord, visible)
        record.observed_at = now - timedelta(days=2)
    assert (await projects.search(ProjectSearchRequest())).total == 1
    assert (await projects.get(visible)).freshness == "stale"
    assert (
        await projects.search(ProjectSearchRequest(criteria=ProjectCriteria(include_stale=True)))
    ).total == 2
    projects.settings.env = "demo"
    assert (
        await projects.search(ProjectSearchRequest())
    ).total == 2  # demo + other current public project
    async with projects.db.sessions.begin() as session:
        provider = await session.get(Provider, "public-b")
        provider.enabled = False
    assert await projects.get(project_id("public-b", "complex")) is None
    assert {row.provider_id for row in (await projects.sources()).items} == {"public-a", "demo-a"}


async def test_layouts_have_independent_evidence_and_partial_metadata_retains_them(projects):
    now = utcnow()
    layout = {
        "external_id": "plan",
        "name": "План 2-комнатный",
        "rooms": 2,
        "area_m2": 70,
        "image_url": "https://example.org/layout.png",
        "source_url": "https://example.org/layout",
        "observed_at": now - timedelta(hours=1),
    }
    await _public(projects, [_input(now=now, layouts=[layout])])
    await _public(projects, [_input(now=now + timedelta(seconds=1))], as_of=now + timedelta(seconds=2))
    record_id = project_id("public-a", "complex")
    plans = await projects.layouts(record_id)
    assert len(plans) == 1
    plan = await projects.layout(plans[0].id)
    assert plan.kind == "layout_type" and plan.rooms == 2 and plan.area_m2 == 70
    assert plan.observed_at == layout["observed_at"]
    assert not hasattr(plan, "price_kzt") and not hasattr(plan, "floor") and not hasattr(plan, "status")
    assert (await projects.get(record_id)).available_layout_count == 1
    assert (await projects.apartments(record_id)).total == 0


async def test_operating_vs_planned_infrastructure_is_preserved(projects):
    now = utcnow()
    await _public(projects, [_input(latitude=51.1, longitude=71.4)])
    async with projects.db.sessions.begin() as session:
        session.add_all(
            [
                ProjectFact(
                    provider_id="public-a",
                    scope_type="complex",
                    scope_id="complex",
                    kind="school",
                    name="Planned school",
                    state="planned",
                    relation="within",
                    evidence="Запланирована школа",
                    source_url="https://example.org/school",
                    observed_at=now,
                    expires_at=now + timedelta(days=1),
                ),
                Place(
                    id="park",
                    city="Астана",
                    kind="park",
                    state="operating",
                    name="Public park",
                    latitude=51.101,
                    longitude=71.4,
                    source_url="https://example.org/park",
                    observed_at=now,
                ),
                Place(
                    id="school",
                    city="Астана",
                    kind="school",
                    state="planned",
                    name="Planned school",
                    latitude=51.101,
                    longitude=71.4,
                    source_url="https://example.org/school",
                    observed_at=now,
                ),
            ]
        )
    assert (
        await projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(required_amenities=["school"], amenity_scope="complex")
            )
        )
    ).total == 0
    assert (
        await projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(required_amenities=["park"], amenity_radius_m=200)
            )
        )
    ).total == 1
    assert (
        await projects.search(
            ProjectSearchRequest(
                criteria=ProjectCriteria(required_amenities=["school"], amenity_radius_m=200)
            )
        )
    ).total == 0
    detail = await projects.get(project_id("public-a", "complex"))
    assert detail.project_facts[0].state == "planned"
    assert {amenity.state for amenity in detail.amenities} == {"operating", "planned"}


async def test_apartment_keyset_scope_and_compare_unknown_values(projects):
    await _public(projects, [_input(), _input("other")])
    await _lots(projects, [_lot("one"), _lot("two"), _lot("three", price_kzt=30_000_000)])
    record_id = project_id("public-a", "complex")
    page = await projects.apartments(record_id, limit=1)
    seen = [page.items[0].id]
    while page.next_cursor:
        page = await projects.apartments(record_id, limit=1, cursor=page.next_cursor)
        seen.extend(item.id for item in page.items)
    assert len(seen) == len(set(seen)) == 3
    first = await projects.apartments(record_id, limit=1)
    with pytest.raises(ValueError, match="invalid_cursor"):
        await projects.apartments(project_id("public-a", "other"), cursor=first.next_cursor)
    compared = await projects.compare([record_id, project_id("public-a", "other")])
    row = next(row for row in compared.rows if row.key == "published_starting_price")
    assert all(value.value is None and value.source_url is None for value in row.values)
    row = next(row for row in compared.rows if row.key == "observed_listing_minimum")
    assert row.values[0].value == 20_000_000 and row.values[1].value is None


async def test_reads_never_call_provider_and_do_not_write(projects, monkeypatch):
    await _public(projects, [_input()])

    async def forbidden(*args, **kwargs):
        pytest.fail("Read attempted provider/model refresh")

    monkeypatch.setattr(projects.catalog, "refresh_provider", forbidden)
    monkeypatch.setattr(projects.catalog, "refresh_events", forbidden)
    async with projects.db.sessions() as session:
        before = [
            (row.id, row.version, row.observed_at, row.received_at)
            for row in (await session.scalars(select(ProjectRecord))).all()
        ]
    await projects.search(ProjectSearchRequest())
    await projects.map(ProjectSearchRequest())
    await projects.facets()
    await projects.sources()
    await projects.get(project_id("public-a", "complex"))
    await projects.layouts(project_id("public-a", "complex"))
    async with projects.db.sessions() as session:
        after = [
            (row.id, row.version, row.observed_at, row.received_at)
            for row in (await session.scalars(select(ProjectRecord))).all()
        ]
    assert before == after


def test_project_predicate_compiles_as_postgresql_sql_without_python_catalog_filtering():
    source = SimpleNamespace(id="public", public_data=True, demo=False)
    catalog = SimpleNamespace(providers={source.id: source})
    service = Projects(None, None, catalog, Settings(_env_file=None, env="test"))
    statement, _ = service._statement(
        ProjectCriteria(
            rooms=[2],
            price_mode="observed_listing_minimum",
            price_max=30_000_000,
            required_amenities=["park"],
            stages=["under_construction"],
        ),
        utcnow(),
    )
    sql = str(statement.limit(21).compile(dialect=postgresql.dialect()))
    assert "GROUP BY" in sql and "LIMIT" in sql and "EXISTS" in sql
    assert "apartments.rooms" in sql and "apartments.price_kzt" in sql


async def test_actual_public_metadata_can_promote_newer_lot_projection_without_freshening(projects):
    now = utcnow()
    await _lots(projects, [_lot("newer-lot")], observed=now)
    original = _input(
        now=now - timedelta(hours=2), name="Published name", published_starting_price_kzt=60_000_000
    )
    await _public(projects, [original])
    result = await projects.get(project_id("public-a", "complex"))
    assert result.record_origin == "public_project"
    assert result.observed_at == original.observed_at
    assert result.name == "Published name"
    assert result.published_starting_price.amount_kzt == 60_000_000
    assert result.observed_listing_minimum.amount_kzt == 20_000_000


async def test_large_kzt_prices_and_cyrillic_search_are_sql_safe(projects):
    await _public(
        projects,
        [
            _input(
                name="ЖК Публичный",
                published_starting_price_kzt=3_500_000_000,
                developer_name="Опубликованный Девелопер",
            )
        ],
    )
    found = await projects.search(
        ProjectSearchRequest(criteria=ProjectCriteria(q="жк пУБЛИч", price_min=3_000_000_000))
    )
    assert found.total == 1 and found.items[0].display_price.amount_kzt == 3_500_000_000
    assert (
        await projects.search(
            ProjectSearchRequest(criteria=ProjectCriteria(q="опубликованный девелопер"))
        )
    ).total == 1
    statement, _ = projects._statement(ProjectCriteria(price_max=4_000_000_000), utcnow())
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "AS BIGINT" in sql


def test_project_and_layout_uuid_identity_is_unambiguous_for_external_colons():
    assert project_id("provider:a", "b") != project_id("provider", "a:b")
    assert layout_id("provider", "a:b", "c") != layout_id("provider", "a", "b:c")
    assert project_id("provider", "complex") == project_id("provider", "complex")


async def test_summary_payloads_keep_counts_and_explicit_detail_contains_materials(projects):
    now = utcnow()
    layouts = [
        {
            "external_id": str(index),
            "name": "План " + str(index),
            "source_url": "https://example.org/layout/" + str(index),
            "observed_at": now,
        }
        for index in range(35)
    ]
    images = [
        {
            "url": "https://example.org/image/" + str(index),
            "source_url": "https://example.org/project",
            "kind": "facade",
            "observed_at": now,
        }
        for index in range(10)
    ]
    docs = [
        {
            "name": "Public document",
            "url": "https://example.org/document",
            "source_url": "https://example.org/project",
            "observed_at": now,
        }
    ]
    buildings = [
        {
            "external_id": "building",
            "name": "Корпус 1",
            "source_url": "https://example.org/project",
            "observed_at": now,
        }
    ]
    await _public(
        projects,
        [
            _input(
                layouts=layouts,
                images=images,
                documents=docs,
                buildings=buildings,
                latitude=51.1,
                longitude=71.4,
                website_url="https://example.org/project",
                website_scope="project",
            ),
            _input("other"),
        ],
    )
    record_id = project_id("public-a", "complex")
    full = await projects.get(record_id)
    assert len(full.layouts) == 35 and len(full.images) == 10 and full.documents and full.buildings
    assert full.website_url == "https://example.org/project" and full.website_scope == "project"
    summary = next(
        item for item in (await projects.search(ProjectSearchRequest())).items if item.id == record_id
    )
    mapped = (await projects.map(ProjectSearchRequest())).items[0]
    compared = (await projects.compare([record_id, project_id("public-a", "other")])).projects[0]
    for item in (summary, mapped, compared):
        assert not item.layouts and not item.documents and not item.buildings
        assert item.available_layout_count == 35 and len(item.images) == 1
        assert item.website_scope == "project"
    assert len(await projects.layouts(record_id)) == 35


async def test_legacy_bootstrap_uses_actual_public_rows_preserves_dates_and_is_idempotent(projects):
    now = utcnow() - timedelta(days=2)
    async with projects.db.sessions.begin() as session:
        for provider_id in ("public-a", "public-b", "partner", "demo-a"):
            lot = _lot("stored")
            session.add(
                Apartment(
                    id=listing_id(provider_id, lot.external_id),
                    provider_id=provider_id,
                    observed_at=now,
                    received_at=now + timedelta(seconds=5),
                    **lot.model_dump(mode="json", exclude={"amenities"}),
                )
            )
    assert await bootstrap_legacy_projects(projects.db, projects.catalog, projects.settings) == 2
    project = await projects.get(project_id("public-a", "complex"))
    assert project.observed_at == now and project.received_at == now + timedelta(seconds=5)
    assert project.record_origin == "observed_lots" and project.freshness == "stale"
    assert project.observed_listing_minimum is None  # Old stored lot cannot establish a fresh price.
    assert await projects.get(project_id("partner", "complex")) is None
    assert await bootstrap_legacy_projects(projects.db, projects.catalog, projects.settings) == 0
    repeated = await projects.get(project.id)
    assert repeated.version == project.version and repeated.observed_at == project.observed_at
    assert repeated.received_at == project.received_at
    projects.settings.env = "demo"
    assert await bootstrap_legacy_projects(projects.db, projects.catalog, projects.settings) == 1
    assert (await projects.get(project_id("demo-a", "complex"))).provenance == "demo"


async def test_get_many_preserves_order_hides_sources_and_reads_stale_compact_saved_cards(projects):
    now = utcnow()
    layout = {
        "external_id": "plan",
        "name": "План",
        "source_url": "https://example.org/layout",
        "observed_at": now,
    }
    await _public(projects, [_input("first", layouts=[layout]), _input("second")])
    await _public(projects, [_input("hidden")], "partner")
    first_id = project_id("public-a", "first")
    second_id = project_id("public-a", "second")
    async with projects.db.sessions.begin() as session:
        record = await session.get(ProjectRecord, first_id)
        record.observed_at = now - timedelta(days=2)
    items = await projects.get_many([second_id, project_id("partner", "hidden"), first_id])
    assert [item.id for item in items] == [second_id, first_id]
    assert items[1].freshness == "stale" and not items[1].layouts
    assert items[1].available_layout_count == 1
    full = await projects.get_many([first_id], full=True)
    assert len(full[0].layouts) == 1
    assert await projects.get_many([]) == []
    with pytest.raises(ValueError, match="invalid_limit"):
        await projects.get_many([first_id] * 201)


async def test_facets_return_typed_supported_price_modes_with_both_sources(projects):
    await _public(projects, [_input(published_starting_price_kzt=35_000_000), _input("unknown")])
    await _lots(projects, [_lot("published-lot", price_kzt=25_000_000)])
    facets = await projects.facets()
    assert [(mode.value, mode.count) for mode in facets.price_modes] == [
        ("published_starting_price", 1),
        ("observed_listing_minimum", 1),
    ]
    assert facets.unknown_published_price_count == 1
    assert facets.unknown_observed_price_count == 1
    assert {mode.value for mode in facets.price_modes} <= {
        "published_starting_price",
        "observed_listing_minimum",
    }


async def test_lot_specific_finish_completion_and_conflicting_locations_are_not_project_facts(projects):
    await _lots(
        projects,
        [
            _lot("building-one", finish="Чистовая", completion="2027", latitude=51.1, longitude=71.4),
            _lot("building-two", finish="Черновая", completion="2028", latitude=51.2, longitude=71.5),
        ],
    )
    async with projects.db.sessions.begin() as session:
        lot = await session.get(Apartment, listing_id("public-a", "building-two"))
        lot.address = "Другой корпус"
        await session.flush()
        await project_projection_from_lots(session, "public-a", [lot], utcnow())
    detail = await projects.get(project_id("public-a", "complex"))
    assert detail.finish is None and detail.completion is None
    assert detail.latitude is None and detail.longitude is None and detail.address is None
    lots = (await projects.apartments(detail.id)).items
    assert {lot.finish for lot in lots} == {"Чистовая", "Черновая"}
    assert {lot.completion for lot in lots} == {"2027", "2028"}


async def test_layout_observations_update_independently_of_same_or_older_project_observation(projects):
    now = utcnow()
    old_layout = {
        "external_id": "plan",
        "name": "Original plan",
        "rooms": 2,
        "source_url": "https://example.org/layout",
        "observed_at": now - timedelta(hours=3),
    }
    core = _input(
        now=now - timedelta(hours=1), published_starting_price_kzt=50_000_000, layouts=[old_layout]
    )
    await _public(projects, [core])
    original = await projects.get(project_id("public-a", "complex"))
    newer_layout = {**old_layout, "name": "Updated plan", "observed_at": now}
    assert (
        await _public(
            projects,
            [core.model_copy(update={"layouts": [type(core.layouts[0]).model_validate(newer_layout)]})],
        )
        == 1
    )
    updated = await projects.get(original.id)
    assert updated.version == original.version and updated.observed_at == original.observed_at
    assert updated.received_at == original.received_at and updated.layouts[0].name == "Updated plan"
    newest_layout = {**newer_layout, "name": "Newest plan", "observed_at": now + timedelta(seconds=1)}
    older_core = core.model_copy(
        update={
            "observed_at": now - timedelta(hours=2),
            "published_starting_price_kzt": 10_000_000,
            "layouts": [type(core.layouts[0]).model_validate(newest_layout)],
        }
    )
    await _public(projects, [older_core], as_of=now + timedelta(seconds=2))
    updated = await projects.get(original.id)
    assert (
        updated.published_starting_price.amount_kzt == 50_000_000
        and updated.observed_at == original.observed_at
    )
    assert updated.layouts[0].name == "Newest plan"
    conflicting = {**newest_layout, "name": "Conflicting bytes"}
    invalid = older_core.model_copy(
        update={"layouts": [type(core.layouts[0]).model_validate(conflicting)]}
    )
    with pytest.raises(SourceError, match="layout_observation_conflict"):
        await _public(
            projects, [_input("would-create-child-conflict"), invalid], as_of=now + timedelta(seconds=2)
        )
    assert await projects.get(project_id("public-a", "would-create-child-conflict")) is None
    assert (await projects.get(original.id)).layouts[0].name == "Newest plan"


async def test_catalog_source_actual_urls_and_public_bigville_metadata(projects):
    projects.catalog.providers["public-a"].config = SimpleNamespace(
        base_url="https://example.org/public-feed"
    )
    await _public(
        projects,
        [
            _input(
                website_url="https://example.org/project",
                website_scope="project",
                bigville_id="published-bigville",
                bigville_name="Published Bigville",
            )
        ],
    )
    source = next(item for item in (await projects.sources()).items if item.provider_id == "public-a")
    assert source.source_url == "https://example.org/public-feed"
    assert source.website_url == "https://example.org/project" and source.website_scope == "project"
    project = await projects.get(project_id("public-a", "complex"))
    assert project.bigville_id == "published-bigville" and project.bigville_name == "Published Bigville"


async def test_grouped_projection_and_all_lot_price_aggregates_execute_on_supported_database(projects):
    # On the explicit PG test URL this executes the CASE grouping with asyncpg
    # numeric bind parameters, which compilation-only checks cannot validate.
    await _lots(
        projects,
        [
            _lot("one", "group", rooms=1, price_kzt=20_000_000),
            _lot("two", "group", rooms=2, price_kzt=35_000_000),
            _lot("single", None, price_kzt=45_000_000),
        ],
    )
    page = await projects.search(
        ProjectSearchRequest(criteria=ProjectCriteria(price_mode="observed_listing_minimum"))
    )
    assert page.total == 2
    grouped = next(item for item in page.items if item.external_id == "group")
    assert grouped.published_lot_count == grouped.matched_lot_count == 2
    assert grouped.observed_listing_minimum.amount_kzt == 20_000_000
    filtered = await projects.search(
        ProjectSearchRequest(
            criteria=ProjectCriteria(
                rooms=[2], price_mode="observed_listing_minimum", price_max=40_000_000
            )
        )
    )
    assert filtered.total == 1 and filtered.items[0].matched_lot_count == 1
