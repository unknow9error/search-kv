"""Optional declared public JSON project feed. No authentication or guessed source API.

The configured HTTPS base URL must expose GET /projects with the exact envelope
{as_of, projects}. Each project retains its own source URL and observation date.
"""

import asyncio
import json
from datetime import datetime

import httpx
from pydantic import Field, field_validator, model_validator

from app.domain.project_input import ProjectInput
from app.domain.schemas import POIInput, Snapshot, StrictModel, normalize_city
from app.providers.base import SourceError
from app.providers.json_feed import FeedConfig


class PublicProjectFeedPayload(StrictModel):
    as_of: datetime
    projects: list[ProjectInput] = Field(max_length=20000)

    _timestamp = field_validator("as_of")(POIInput.timestamp.__func__)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({project.external_id for project in self.projects}) != len(self.projects):
            raise ValueError("Duplicate project external IDs in public feed")
        return self


class PublicProjectFeedProvider:
    demo = False
    public_data = True

    def __init__(
        self,
        config: FeedConfig,
        client: httpx.AsyncClient,
        max_items: int,
        timeout_seconds: float = 6,
    ):
        if config.kind != "public_project_feed" or config.token_env is not None:
            raise ValueError("Public project feeds require public_project_feed and no token_env")
        if max_items < 1 or timeout_seconds <= 0:
            raise ValueError("Public feed limits must be positive")
        self.config, self.client = config, client
        self.id, self.name, self.cities = config.id, config.name, config.cities
        self.max_items, self.timeout_seconds = min(max_items, 20000), timeout_seconds

    async def _json(self, params: dict | None = None) -> dict:
        request = self.client.build_request(
            "GET",
            self.config.base_url.rstrip("/") + "/projects",
            params=params,
            headers={"Accept": "application/json"},
            timeout=self.timeout_seconds,
        )
        # Shared clients must not accidentally turn this into an authenticated source.
        for header in ("Authorization", "Cookie", "Proxy-Authorization"):
            request.headers.pop(header, None)
        async with asyncio.timeout(self.timeout_seconds):
            response = await self.client.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                if response.status_code == 429:
                    raise SourceError("rate_limited")
                if response.status_code != 200:
                    raise SourceError("upstream_error")
                if "json" not in response.headers.get("content-type", ""):
                    raise SourceError("invalid_content_type")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise SourceError("response_too_large")
            finally:
                await response.aclose()
        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise SourceError("invalid_json") from exc
        if not isinstance(payload, dict):
            raise SourceError("invalid_schema")
        return payload

    async def fetch(self, city: str | None = None) -> Snapshot:
        city = normalize_city(city)
        if city is not None and city not in self.cities:
            raise SourceError("unsupported_city")
        payload = PublicProjectFeedPayload.model_validate(
            await self._json({"city": city} if city else None)
        )
        if len(payload.projects) > self.max_items:
            raise SourceError("catalog_limit")
        if any(
            project.city not in self.cities or (city is not None and project.city != city)
            for project in payload.projects
        ):
            raise SourceError("project_city_scope_mismatch")
        return Snapshot(
            as_of=payload.as_of,
            complete=False,
            items=[],
            scope_city=city,
            projects=payload.projects,
        )

    async def verify(self, external_id: str) -> Snapshot:
        # The public envelope declares no per-lot or per-project verification endpoint.
        raise SourceError("unsupported_verification")
