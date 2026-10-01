from datetime import timedelta

from app.domain.models import utcnow
from app.domain.schemas import Preferences, ProjectContextInput, Snapshot
from app.services.assistant import explain_listings
from app.services.catalog import listing_id
from app.services.project_context import ingest_context


async def test_planned_bigville_school_is_not_existing_nearby_school(app):
    catalog = app.state.catalog
    provider = catalog.providers["demo-garden"]
    raw = provider.records()[0].model_copy(
        update={
            "amenities": [],
            "complex_id": "one",
            "bigville_id": "big",
            "bigville_name": "Пример бигвилля",
        }
    )
    await catalog.ingest(provider.id, Snapshot(as_of=utcnow(), complete=False, items=[raw]))
    context = ProjectContextInput(
        provider_id=provider.id,
        scope_type="bigville",
        scope_id="big",
        source_url="https://example.com/project",
        observed_at=utcnow(),
        expires_at=utcnow() + timedelta(days=30),
        facts=[
            {
                "kind": "school",
                "name": "Школа в проекте",
                "state": "planned",
                "expected_opening": "2028",
                "evidence": "В проекте запланировано строительство школы.",
            }
        ],
    )
    await ingest_context(catalog.db, context)
    listing = await catalog.get(listing_id(provider.id, raw.external_id))
    assert listing.bigville_name == "Пример бигвилля"
    assert listing.project_facts[0].state == "planned"
    assert listing.amenities == []
    assert await catalog.search(Preferences(required_amenities=["school"])) == []
    explanation = explain_listings([listing])
    assert "запланировано" in explanation and "работа объекта пока не подтверждена" in explanation


async def test_context_is_scoped_and_old_document_cannot_replace_new(app):
    catalog, now = app.state.catalog, utcnow()
    provider = catalog.providers["demo-garden"]
    raw = provider.records()[0].model_copy(update={"complex_id": "one", "amenities": []})
    other = provider.records()[1].model_copy(update={"complex_id": "two", "amenities": []})
    await catalog.ingest(provider.id, Snapshot(as_of=now, complete=False, items=[raw, other]))
    context = ProjectContextInput(
        provider_id=provider.id,
        scope_type="complex",
        scope_id="one",
        source_url="https://example.com/project",
        observed_at=now,
        expires_at=now + timedelta(days=20),
        facts=[
            {
                "kind": "school",
                "name": "Школа",
                "state": "under_construction",
                "evidence": "Школа строится.",
            }
        ],
    )
    await ingest_context(catalog.db, context)
    old = context.model_copy(update={"observed_at": now - timedelta(days=1), "facts": []})
    assert await ingest_context(catalog.db, old) == 0
    one = await catalog.get(listing_id(provider.id, raw.external_id))
    two = await catalog.get(listing_id(provider.id, other.external_id))
    assert len(one.project_facts) == 1 and two.project_facts == []


async def test_developer_operating_school_can_match_bigville_without_osm(app):
    catalog = app.state.catalog
    provider = catalog.providers["demo-garden"]
    raw = provider.records()[0].model_copy(
        update={
            "amenities": [],
            "latitude": None,
            "longitude": None,
            "complex_id": "one",
            "bigville_id": "big",
        }
    )
    await catalog.ingest(provider.id, Snapshot(as_of=utcnow(), complete=False, items=[raw]))
    now = utcnow()
    context = ProjectContextInput(
        provider_id=provider.id,
        scope_type="bigville",
        scope_id="big",
        source_url="https://example.com/developer",
        observed_at=now,
        expires_at=now + timedelta(days=30),
        facts=[
            {
                "kind": "school",
                "name": "Школа",
                "state": "operating",
                "relation": "within",
                "evidence": "Застройщик указывает работающую школу в бигвилле.",
            }
        ],
    )
    await ingest_context(catalog.db, context)
    results = await catalog.search(Preferences(required_amenities=["school"], amenity_scope="bigville"))
    assert len(results) == 1 and "Застройщик" in results[0].reasons[0]
    assert await catalog.search(Preferences(required_amenities=["school"], amenity_scope="nearby")) == []
    empty = context.model_copy(update={"observed_at": now + timedelta(seconds=1), "facts": []})
    await ingest_context(catalog.db, empty)
    assert await ingest_context(catalog.db, context) == 0
    assert (
        await catalog.search(Preferences(required_amenities=["school"], amenity_scope="bigville")) == []
    )
