import json
from email.utils import format_datetime
from uuid import uuid4

import httpx
import pytest

from app.core.coordination import Coordination
from app.domain.models import utcnow
from app.providers.base import SourceError
from app.providers.bi_group import APARTMENT_TYPE, CITY_IDS, FREE_STATUS, BIGroupProvider


def raw_listing(**changes):
    return {
        "uuid": str(uuid4()),
        "realEstateUUID": "project",
        "propertyType": {"uuid": APARTMENT_TYPE},
        "placementStatusId": FREE_STATUS,
        "placementStatusName": "Свободно",
        "realEstateName": "Contract fixture",
        "roomCount": 2,
        "square": 55.5,
        "floor": 3,
        "maxFloor": 9,
        "totalPrice": 32_000_000,
        "blockAddress": "Contract address",
        **changes,
    }


PROJECTS = {
    "project": {
        "cityUUID": CITY_IDS["Астана"],
        "name": "Contract fixture",
        "latitude": 51.12,
        "longitude": 71.41,
    }
}


def test_bi_discount_and_marketing_flags_not_availability():
    provider = BIGroupProvider(None, Coordination(""))
    raw = raw_listing(totalPriceWithDiscount=1, isSale=True, canBuy=True)
    normalized = provider.normalize(raw, PROJECTS, "Астана")
    assert normalized.price_kzt == 32_000_000 and normalized.status == "available"
    unknown = provider.normalize(
        {**raw, "placementStatusName": "Расторжение", "placementStatusId": "unmapped"}, PROJECTS
    )
    assert unknown.status == "unknown"
    booked = provider.normalize({**raw, "individualBooking": True}, PROJECTS)
    assert booked.status == "reserved"


def test_bi_does_not_invent_city_or_coordinates():
    provider = BIGroupProvider(None, Coordination(""))
    assert provider.normalize(raw_listing(), PROJECTS, "Алматы") is None
    assert provider.normalize(raw_listing(), {}) is None
    result = provider.normalize(raw_listing(), {"project": {"cityUUID": CITY_IDS["Астана"]}})
    assert result.latitude is None and result.longitude is None and result.amenities == []


async def test_bi_verification_checks_identity():
    async def handler(request):
        return httpx.Response(
            200, headers={"Date": format_datetime(utcnow())}, json={"placementUUID": str(uuid4())}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BIGroupProvider(client, Coordination(""))
        with pytest.raises(SourceError, match="verification_identity_mismatch"):
            await provider.verify(str(uuid4()))


async def test_bi_cache_age_preserves_observation_time():
    now = utcnow().replace(microsecond=0)

    async def handler(request):
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(200, headers={"Date": format_datetime(now), "Age": "600"}, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BIGroupProvider(client, Coordination(""))
        _, observed = await provider.read("filter", {})
        assert (now - observed).total_seconds() == 600


async def test_bi_pages_commit_incrementally_and_use_actual_filter_fields():
    records = [raw_listing() for _ in range(150)]

    async def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("realEstateList"):
            return httpx.Response(
                200,
                headers={"Date": format_datetime(utcnow())},
                json={"realEstates": [{"uuid": "project", **PROJECTS["project"]}]},
            )
        assert body["cityUUID"] == CITY_IDS["Астана"]
        assert body["priceMax"] == 35_000_000
        assert body["placementStatusIds"] == [FREE_STATUS]
        page = records if body["pageNo"] == 1 else []
        return httpx.Response(
            200, headers={"Date": format_datetime(utcnow())}, json={"placements": page}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from app.domain.schemas import Preferences

        provider = BIGroupProvider(client, Coordination(""))
        pages = [
            x
            async for x in provider.fetch_pages(
                "Астана", Preferences(city="Астана", budget_max=35_000_000)
            )
        ]
        assert len(pages) == 2 and len(pages[0].items) == 150
        assert all(not x.complete for x in pages)
