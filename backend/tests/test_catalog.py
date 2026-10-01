from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.domain.models import Apartment, ImportRun, utcnow
from app.domain.schemas import Availability, Preferences, Snapshot, distance_m
from app.services.catalog import listing_id


async def test_cache_search_and_detail_never_invoke_model(app, client, seeded, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Catalog invoked AI")

    monkeypatch.setattr(app.state.assistant, "_parse", forbidden)
    response = await client.post(
        "/v1/search", json={"preferences": {"city": "Астана", "budget_max": 35_000_000, "rooms": [2]}}
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items and all(
        x["price_kzt"] <= 35_000_000 and x["rooms"] == 2 and x["city"] == "Астана" for x in items
    )
    assert (await client.get("/v1/apartments/" + items[0]["id"])).status_code == 200


async def test_numeric_and_amenity_hard_filters(seeded):
    rows = await seeded.search(
        Preferences(
            city="Астана", floor_min=3, floor_max=5, required_amenities=["school"], amenity_radius_m=700
        )
    )
    assert rows
    assert all(3 <= x.floor <= 5 for x in rows)
    assert all(any(a.kind == "school" and a.distance_m <= 700 for a in x.amenities) for x in rows)
    assert await seeded.search(Preferences(city="Астана", required_amenities=["transit"])) == []


async def test_old_snapshot_cannot_resurrect_sold(seeded):
    provider = seeded.providers["demo-garden"]
    raw = provider.records()[0]
    now = utcnow()
    await seeded.ingest(
        provider.id,
        Snapshot(
            as_of=now, complete=False, items=[raw.model_copy(update={"status": Availability.sold})]
        ),
    )
    await seeded.ingest(
        provider.id, Snapshot(as_of=now - timedelta(hours=1), complete=False, items=[raw])
    )
    listing = await seeded.get(listing_id(provider.id, raw.external_id))
    assert listing.status == "sold"
    assert listing.id not in [x.id for x in await seeded.search(Preferences())]


async def test_incomplete_snapshot_does_not_remove_other_records(seeded):
    provider = seeded.providers["demo-garden"]
    before = await seeded.search(Preferences(), 50)
    await seeded.ingest(provider.id, Snapshot(as_of=utcnow(), complete=False, items=[]))
    after = await seeded.search(Preferences(), 50)
    assert {x.id for x in before} == {x.id for x in after}


async def test_complete_zero_snapshot_only_changes_declared_city(seeded):
    await seeded.ingest(
        "demo-garden",
        Snapshot(as_of=utcnow(), complete=True, allow_empty=True, scope_city="Астана", items=[]),
    )
    astana = await seeded.search(Preferences(city="Астана"))
    almaty = await seeded.search(Preferences(city="Алматы"))
    assert not any(x.provider_id == "demo-garden" for x in astana)
    assert any(x.provider_id == "demo-garden" for x in almaty)
    async with seeded.db.sessions() as db:
        row = await db.get(Apartment, listing_id("demo-garden", "0-0"))
        assert row.status == "unknown"
        assert await db.scalar(
            select(ImportRun).where(ImportRun.count == 0, ImportRun.outcome == "success")
        )


def test_empty_complete_requires_explicit_contract():
    with pytest.raises(ValidationError):
        Snapshot(as_of=utcnow(), complete=True, items=[])


async def test_stale_rows_expire_but_remain_readable_for_saved_item(seeded):
    provider = seeded.providers["demo-garden"]
    raw = provider.records()[0]
    async with seeded.db.sessions.begin() as db:
        row = await db.get(Apartment, listing_id(provider.id, raw.external_id))
        row.observed_at = utcnow() - timedelta(days=2)
    listing = await seeded.get(listing_id(provider.id, raw.external_id))
    assert listing.freshness == "stale"
    assert listing.id not in [x.id for x in await seeded.search(Preferences())]


async def test_fast_source_streams_before_slow_source(app):
    slow = app.state.catalog.providers["demo-river"]
    slow.delay = 0.5
    events = app.state.catalog.refresh_events("Астана")
    first = await anext(events)
    assert first["provider_id"] == "demo-garden" and first["status"] == "complete"
    assert len(await app.state.catalog.search(Preferences(city="Астана"))) == 6
    rest = [x async for x in events]
    assert rest[0]["status"] == "timeout"


async def test_provider_failure_does_not_freshen_cache(app, seeded):
    source = app.state.catalog.providers["demo-garden"]
    await seeded.coordination.set("provider:" + source.id + ":circuit", "1", 60)
    result = await seeded.verify(listing_id(source.id, "0-0"))
    assert result["verification"] == "unconfirmed"
    assert result["listing"]["provenance"] == "demo"


async def test_ingest_idempotent_identity(seeded):
    provider = seeded.providers["demo-garden"]
    before = {x.id for x in await seeded.search(Preferences(), 50)}
    await seeded.ingest(provider.id, await provider.fetch())
    after = {x.id for x in await seeded.search(Preferences(), 50)}
    assert before == after


def test_distance_is_computed_not_trusted():
    assert distance_m(51.1, 71.4, 51.1, 71.4) == 0
    assert 1100 < distance_m(51.1, 71.4, 51.11, 71.4) < 1120
