"""Public BI Group sales-picker contract observed 2026-09-12.

Read-only POSTs; no client/deal identifiers, cookies, reservations or discount assumptions.
OpenAPI: https://apigw.bi.group/sales-picker/v3/api-docs
"""

import asyncio
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.core.coordination import Coordination
from app.domain.models import utcnow
from app.domain.schemas import ListingInput, Preferences, Snapshot
from app.providers.base import SourceError

CITY_IDS = {
    "Астана": "4c0fe725-4b6f-11e8-80cf-bb580b2abfef",
    "Алматы": "6ba77338-4db7-11e8-80cf-bb580b2abfef",
    "Шымкент": "cf5ad35a-9bc1-11e8-80d7-00155da7893d",
    "Атырау": "ed9bc954-8e26-11e8-80d0-00155da78938",
    "Караганда": "7efa680c-5423-11e8-80d6-00155da7893d",
}
APARTMENT_TYPE = "5990a172-812a-4fee-b4f5-c860cca824d7"
FREE_STATUS = "1a85b7a9-7adc-11e9-a831-00155d10652c"
STATUS_IDS = {FREE_STATUS: "available", "1a85b7a2-7adc-11e9-a831-00155d10652c": "reserved"}


class BIGroupProvider:
    id, name, demo = "bi-group", "BI Group", False
    origin = "https://apigw.bi.group/sales-picker/microfe-v3/"

    def __init__(self, client: httpx.AsyncClient, coordination: Coordination, cities=None):
        self.client, self.coordination = client, coordination
        self.cities = cities or list(CITY_IDS)
        if any(x not in CITY_IDS for x in self.cities):
            raise ValueError("Unmapped BI city; discover its UUID before enabling")

    async def read(self, endpoint: str, body: dict) -> tuple[dict, datetime]:
        if endpoint not in {"placementList", "placement", "realEstateList", "filter"}:
            raise SourceError("unsupported_endpoint")
        async with self.client.stream(
            "POST",
            self.origin + endpoint,
            json=body,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.status_code == 429:
                raise SourceError("rate_limited")
            if response.status_code != 200:
                raise SourceError("upstream_error")
            if "json" not in response.headers.get("content-type", ""):
                raise SourceError("invalid_content_type")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 8_000_000:
                    raise SourceError("response_too_large")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise SourceError("invalid_schema")
            # Use HTTP Date minus cache Age. Receiving an old cache must not freshen it.
            try:
                observed = parsedate_to_datetime(response.headers["date"]).astimezone(UTC)
                from datetime import timedelta

                observed -= timedelta(seconds=max(0, int(response.headers.get("age", "0"))))
            except (KeyError, ValueError, TypeError):
                raise SourceError("missing_observation_time") from None
            return result, min(observed, utcnow())

    async def projects(self) -> dict:
        key = "bi:projects:v2"
        if cached := await self.coordination.get(key):
            return json.loads(cached)
        payload, _ = await self.read(
            "realEstateList", {"pageNo": 1, "pageSize": 500, "propertyTypes": [APARTMENT_TYPE]}
        )
        projects = payload.get("realEstates")
        if not isinstance(projects, list) or len(projects) >= 500:
            raise SourceError("project_schema_or_limit")
        result = {
            str(x["uuid"]): {
                k: x.get(k)
                for k in ["name", "cityUUID", "address", "latitude", "longitude", "website", "bigVille"]
            }
            for x in projects
        }
        await self.coordination.set(key, json.dumps(result), 3600)
        return result

    def normalize(self, raw: dict, projects: dict, city: str | None = None) -> ListingInput | None:
        if (raw.get("propertyType") or {}).get("uuid", raw.get("propertyTypeId")) != APARTMENT_TYPE:
            return None
        project = projects.get(str(raw.get("realEstateUUID")), {})
        actual_city = next(
            (name for name, identifier in CITY_IDS.items() if identifier == project.get("cityUUID")),
            None,
        )
        if actual_city not in self.cities or (city and actual_city != city):
            return None
        identifier = str(UUID(raw.get("uuid") or raw["placementUUID"]))
        status_id = str(raw.get("placementStatusId") or raw.get("placementStatusUUID"))
        status = STATUS_IDS.get(status_id, "unknown")
        if status == "available" and raw.get("placementStatusName") != "Свободно":
            status = "unknown"
        if raw.get("individualBooking") is True:
            status = "reserved"
        # A deadline in the past does not prove that a building has been commissioned.
        deadline = raw.get("deadLine")
        completion = "Срок по данным застройщика: " + deadline[:10] if deadline else "Уточняется"
        lat, lon = project.get("latitude"), project.get("longitude")
        if not lat or not lon or not (40 <= lat <= 56 and 46 <= lon <= 88):
            lat, lon = None, None
        website = raw.get("website") or project.get("website") or "https://bi.group/ru/filter/placements"
        if not self._bi_url(website):
            website = "https://bi.group/ru/filter/placements"
        photo = raw.get("photoURL400")
        if not self._bi_url(photo):
            photo = None
        return ListingInput(
            external_id=identifier,
            complex_name=raw.get("realEstateName") or project.get("name"),
            complex_id=str(raw["realEstateUUID"]),
            bigville_id=(project.get("bigVille") or {}).get("id"),
            bigville_name=(project.get("bigVille") or {}).get("name"),
            city=actual_city,
            address=raw.get("blockAddress") or project.get("address") or "Адрес уточняется",
            rooms=raw["roomCount"],
            area_m2=raw["square"],
            floor=raw["floor"],
            total_floors=raw["maxFloor"],
            price_kzt=raw["totalPrice"],
            status=status,
            latitude=lat,
            longitude=lon,
            finish="Чистовая" if raw.get("isRepaired") is True else "Состав отделки уточняется",
            completion=completion,
            source_url=website,
            image_url=photo,
        )

    @staticmethod
    def _bi_url(value):
        if not isinstance(value, str):
            return False
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname is not None
            and (url.hostname == "bi.group" or url.hostname.endswith(".bi.group"))
            and not url.username
            and not url.password
        )

    async def fetch_pages(self, city: str | None = None, preferences: Preferences | None = None):
        projects = await self.projects()
        if city:
            scope = [city]
        else:
            # Rotate cities across worker cycles; a slow first city cannot starve the rest.
            index = int(await self.coordination.get("bi:scan:city-index") or 0) % len(self.cities)
            scope = [self.cities[index]]
            await self.coordination.set("bi:scan:city-index", str((index + 1) % len(self.cities)), 86400)
        for current_city in scope:
            if current_city not in self.cities:
                continue
            cursor_key = "bi:scan:" + current_city
            start_page = 1 if preferences else int(await self.coordination.get(cursor_key) or 1)
            for page in range(start_page, start_page + (3 if preferences else 8)):
                query = {
                    "pageNo": page,
                    "pageSize": 150,
                    "cityUUID": CITY_IDS[current_city],
                    "propertyTypes": [APARTMENT_TYPE],
                }
                if preferences:
                    query["placementStatusIds"] = [FREE_STATUS]
                    for key, value in {
                        "priceMax": preferences.budget_max,
                        "roomCounts": preferences.rooms,
                        "floorMin": preferences.floor_min,
                        "floorMax": preferences.floor_max,
                        "squareMin": preferences.area_min,
                    }.items():
                        if value:
                            query[key] = value
                payload, observed = await self.read("placementList", query)
                rows = payload.get("placements")
                if not isinstance(rows, list) or len(rows) > 150:
                    raise SourceError("invalid_schema")
                items = []
                for row in rows:
                    normalized = self.normalize(row, projects, current_city)
                    if normalized:
                        items.append(normalized)
                # Pagination is not a stable snapshot: never infer sold/absent from these pages.
                yield Snapshot(as_of=observed, complete=False, items=items, scope_city=current_city)
                if not preferences:
                    await self.coordination.set(
                        cursor_key, str(page + 1 if len(rows) == 150 else 1), 86400
                    )
                if len(rows) < 150:
                    break
                await asyncio.sleep(0.15)

    async def fetch(self, city=None):
        async for snapshot in self.fetch_pages(city, Preferences(city=city)):
            return snapshot
        return Snapshot(as_of=utcnow(), complete=False, items=[])

    async def verify(self, external_id: str):
        raw, observed = await self.read("placement", {"placementUUID": str(UUID(external_id))})
        if str(raw.get("placementUUID")) != external_id:
            raise SourceError("verification_identity_mismatch")
        item = self.normalize(raw, await self.projects())
        if not item:
            raise SourceError("verification_unavailable")
        return Snapshot(as_of=observed, complete=False, items=[item])
