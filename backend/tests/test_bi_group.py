import json
from datetime import timedelta
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
        assert len(pages) == 3 and not pages[0].items and len(pages[0].projects) == 1
        assert len(pages[1].items) == 150 and not pages[1].projects
        assert all(not x.complete for x in pages)


async def test_project_directory_cache_preserves_its_own_http_observation():
    now = utcnow().replace(microsecond=0)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Date": format_datetime(now), "Age": "600"},
            json={"realEstates": [{"uuid": "project", **PROJECTS["project"]}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BIGroupProvider(client, Coordination(""))
        first = await provider.projects()
        cached = await provider.projects()
        assert calls == 1 and first == cached
        project = provider.normalize_project("project", cached["project"])
        assert project.observed_at == now - timedelta(minutes=10)


def test_project_directory_does_not_infer_lot_price_stage_or_facade():
    provider = BIGroupProvider(None, Coordination(""))
    project = provider.normalize_project(
        "project",
        {
            **PROJECTS["project"],
            "_observed_at": utcnow().isoformat(),
            "totalPrice": 22_000_000,
            "deadLine": "2020-01-01",
            "photoURL400": "https://bi.group/plan.jpg",
            "website": "https://unrelated.example/marketing",
        },
    )
    assert project.developer_name == "BI Group"
    assert project.published_starting_price_kzt is None
    assert project.stage is None and project.completion is None
    assert project.images == [] and project.layouts == [] and project.documents == []
    assert str(project.source_url) == provider.origin + "realEstateList"
    assert str(project.website_url) == "https://bi.group/ru/filter/placements"
    assert project.website_scope == "provider_catalog"
    assert project.bigville_id is None and project.bigville_name is None
    project_with_website = provider.normalize_project(
        "project",
        {
            **PROJECTS["project"],
            "_observed_at": utcnow().isoformat(),
            "website": "https://bi.group/ru/projects/public-contract",
            "bigVille": {"id": "public-bigville", "name": "Contract bigville"},
        },
    )
    assert str(project_with_website.website_url) == "https://bi.group/ru/projects/public-contract"
    assert project_with_website.website_scope == "project"
    assert str(project_with_website.source_url) == provider.origin + "realEstateList"
    assert project_with_website.bigville_id == "public-bigville"
    assert project_with_website.bigville_name == "Contract bigville"
    assert provider.normalize_project(
        "project", {**PROJECTS["project"], "_observed_at": utcnow().isoformat()}, "Алматы"
    ) is None


async def test_project_directory_is_emitted_before_lot_source_failure():
    placement_called = False

    async def handler(request):
        nonlocal placement_called
        if request.url.path.endswith("realEstateList"):
            return httpx.Response(
                200,
                headers={"Date": format_datetime(utcnow())},
                json={"realEstates": [{"uuid": "project", **PROJECTS["project"]}]},
            )
        placement_called = True
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BIGroupProvider(client, Coordination(""))
        pages = provider.fetch_pages("Астана")
        directory = await anext(pages)
        assert directory.projects[0].external_id == "project"
        assert not directory.items and not directory.complete and not placement_called
        with pytest.raises(SourceError, match="upstream_error"):
            await anext(pages)


async def test_project_without_lots_and_legacy_fetch_interface():
    async def handler(request):
        payload = (
            {"realEstates": [{"uuid": "project", **PROJECTS["project"]}]}
            if request.url.path.endswith("realEstateList")
            else {"placements": []}
        )
        return httpx.Response(200, headers={"Date": format_datetime(utcnow())}, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BIGroupProvider(client, Coordination(""))
        pages = [page async for page in provider.fetch_pages("Астана")]
        assert len(pages) == 2 and len(pages[0].projects) == 1
        assert all(not page.items and not page.complete for page in pages)
        snapshot = await provider.fetch("Астана")
        assert not snapshot.items and snapshot.projects[0].name == "Contract fixture"


async def test_duplicate_project_directory_ids_do_not_overwrite_silently():
    async def handler(request):
        return httpx.Response(
            200,
            headers={"Date": format_datetime(utcnow())},
            json={"realEstates": [{"uuid": "project", **PROJECTS["project"]}] * 2},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError, match="project_schema_or_limit"):
            await BIGroupProvider(client, Coordination("")).projects()
