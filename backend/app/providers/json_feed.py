import ipaddress
import json
import os
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field, TypeAdapter, model_validator

from app.domain.schemas import Snapshot, StrictModel, normalize_city
from app.providers.base import SourceError


class FeedConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    kind: str = "json_feed"
    base_url: str
    cities: list[str]
    token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    enabled: bool = True

    @model_validator(mode="after")
    def validate_origin(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Feed base URL must be a fixed HTTPS origin/path without credentials")
        host = url.hostname.lower()
        if host == "localhost" or host.endswith((".local", ".internal")):
            raise ValueError("Feed host must be public")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError("Feed IP must be public")
        self.cities = [normalize_city(x) for x in self.cities]
        return self


class JsonFeedProvider:
    """Versioned partner feed contract, deliberately not a guessed third-party API."""

    demo = False

    def __init__(self, config: FeedConfig, client: httpx.AsyncClient, max_items: int):
        self.config = config
        self.id, self.name, self.cities = config.id, config.name, config.cities
        self.client, self.max_items = client, max_items
        self.headers = {}
        if config.token_env:
            token = os.environ.get(config.token_env)
            if not token:
                raise ValueError(
                    f"Missing configured credential environment variable: {config.token_env}"
                )
            self.headers["Authorization"] = "Bearer " + token

    async def _json(self, path: str, params=None) -> dict:
        url = self.config.base_url.rstrip("/") + "/" + path
        async with self.client.stream(
            "GET", url, params=params, headers=self.headers, follow_redirects=False
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
                if len(data) > 8 * 1024 * 1024:
                    raise SourceError("response_too_large")
            try:
                result = json.loads(data)
            except ValueError as exc:
                raise SourceError("invalid_json") from exc
            if not isinstance(result, dict):
                raise SourceError("invalid_schema")
            return result

    async def fetch(self, city: str | None = None) -> Snapshot:
        items, seen_cursors = [], set()
        contexts = []
        cursor, as_of, complete, allow_empty = None, None, False, False
        for _ in range(100):
            params = {"limit": 500}
            if city:
                params["city"] = city
            if cursor:
                params["cursor"] = cursor
            page = await self._json("apartments", params)
            page_cursor = page.pop("next_cursor", None)
            snapshot = Snapshot.model_validate(page)
            if as_of is not None and snapshot.as_of != as_of:
                raise SourceError("snapshot_changed_during_pagination")
            as_of, complete, allow_empty = snapshot.as_of, snapshot.complete, snapshot.allow_empty
            items.extend(snapshot.items)
            contexts.extend(snapshot.project_contexts)
            if len(items) > self.max_items:
                raise SourceError("catalog_limit")
            if not page_cursor:
                break
            if not isinstance(page_cursor, str) or len(page_cursor) > 300 or page_cursor in seen_cursors:
                raise SourceError("invalid_cursor")
            seen_cursors.add(page_cursor)
            cursor = page_cursor
        else:
            raise SourceError("pagination_limit")
        return Snapshot(
            as_of=as_of,
            items=items,
            complete=complete,
            allow_empty=allow_empty,
            scope_city=city,
            project_contexts=contexts,
        )

    async def verify(self, external_id: str) -> Snapshot:
        data = await self._json("apartments/" + quote(external_id, safe=""))
        snapshot = Snapshot.model_validate(data)
        if snapshot.complete or len(snapshot.items) != 1 or snapshot.items[0].external_id != external_id:
            raise SourceError("invalid_verification")
        return snapshot


config_adapter = TypeAdapter(list[FeedConfig])
