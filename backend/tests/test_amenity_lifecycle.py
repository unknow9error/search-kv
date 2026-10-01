import pytest
from pydantic import ValidationError

from app.domain.models import utcnow
from app.domain.schemas import Preferences, ProjectFactInput, Snapshot
from app.services.catalog import listing_id


async def test_planned_point_never_counts_as_operating_school(app):
    catalog = app.state.catalog
    provider = catalog.providers["demo-garden"]
    raw = provider.records()[0]
    points = [p.model_copy(update={"state": "planned"}) for p in raw.amenities]
    await catalog.ingest(
        provider.id,
        Snapshot(as_of=utcnow(), complete=False, items=[raw.model_copy(update={"amenities": points})]),
    )
    assert await catalog.search(Preferences(required_amenities=["school"])) == []
    listing = await catalog.get(listing_id(provider.id, raw.external_id))
    assert listing.amenities == []


def test_future_evidence_cannot_claim_operating_school():
    with pytest.raises(ValidationError):
        ProjectFactInput(
            kind="school", name="Школа", state="operating", evidence="Школа будет открыта в 2028 году."
        )
