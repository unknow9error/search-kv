import asyncio
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.domain.models import utcnow
from app.providers.base import SourceError
from app.providers.json_feed import FeedConfig
from app.providers.public_project_feed import PublicProjectFeedProvider


def config(**changes):
    return FeedConfig(
        id="public-contract",
        name="Public contract fixture",
        kind="public_project_feed",
        base_url="https://public.example/catalog",
        cities=["Астана"],
        **changes,
    )


def record(**changes):
    return {
        "external_id": "project-one",
        "name": "Public contract fixture",
        "city": "Астана",
        "source_url": "https://public.example/projects/one",
        "observed_at": (utcnow() - timedelta(days=2)).isoformat(),
        **changes,
    }


def payload(projects=None, **changes):
    return {
        "as_of": utcnow().isoformat(),
        "projects": projects if projects is not None else [record()],
        **changes,
    }


async def test_public_feed_has_no_credentials_and_preserves_source_timestamp():
    data = payload()

    async def handler(request):
        assert request.method == "GET" and request.url.path == "/catalog/projects"
        assert request.url.params["city"] == "Астана"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert "proxy-authorization" not in request.headers
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-token", "Proxy-Authorization": "test-proxy"},
        cookies={"session": "test-cookie"},
        auth=("test-user", "test-password"),
    ) as client:
        snapshot = await PublicProjectFeedProvider(config(), client, 10).fetch("astana")
        assert not snapshot.items and not snapshot.complete and snapshot.scope_city == "Астана"
        assert snapshot.projects[0].observed_at.isoformat() == data["projects"][0]["observed_at"]
        assert snapshot.projects[0].observed_at < snapshot.as_of
        assert snapshot.projects[0].published_starting_price_kzt is None
        assert snapshot.projects[0].stage is None


def test_public_feed_refuses_token_config_and_invalid_source_origin():
    with pytest.raises(ValueError, match="no token_env"):
        PublicProjectFeedProvider(config(token_env="TEST_PUBLIC_FEED_TOKEN"), None, 10)
    with pytest.raises(ValueError, match="public_project_feed"):
        PublicProjectFeedProvider(config().model_copy(update={"kind": "json_feed"}), None, 10)
    for invalid in ("http://public.example", "https://user:password@public.example", "https://127.0.0.1"):
        with pytest.raises(ValidationError):
            FeedConfig(id="test", name="test", kind="public_project_feed", base_url=invalid, cities=["Астана"])


@pytest.mark.parametrize(
    "data",
    [
        payload(extra_unknown=True),
        payload([record(), record()]),
        payload([record(stage="", observed_at=utcnow().replace(tzinfo=None).isoformat())]),
        payload(as_of=utcnow().replace(tzinfo=None).isoformat()),
        payload([record(source_url="https://user:password@public.example/project")]),
    ],
)
async def test_public_feed_rejects_invalid_envelope_and_records(data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    ) as client:
        with pytest.raises(ValidationError):
            await PublicProjectFeedProvider(config(), client, 10).fetch()


async def test_public_feed_rejects_counts_and_unexpected_city():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload([record(), record(external_id="other")]))
        )
    ) as client:
        with pytest.raises(SourceError, match="catalog_limit"):
            await PublicProjectFeedProvider(config(), client, 1).fetch()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload([record(city="Алматы")]))
        )
    ) as client:
        provider = PublicProjectFeedProvider(config(), client, 10)
        with pytest.raises(SourceError, match="unsupported_city"):
            await provider.fetch("Алматы")
        with pytest.raises(SourceError, match="project_city_scope_mismatch"):
            await provider.fetch("Астана")


@pytest.mark.parametrize(
    ("status", "content", "headers", "error"),
    [
        (302, b"", {"Location": "https://redirect.example/projects"}, "upstream_error"),
        (429, b"", {}, "rate_limited"),
        (200, b"<html>not JSON</html>", {"Content-Type": "text/html"}, "invalid_content_type"),
        (200, b"broken", {"Content-Type": "application/json"}, "invalid_json"),
        (200, b"[]", {"Content-Type": "application/json"}, "invalid_schema"),
        (200, b" " * (8 * 1024 * 1024 + 1), {"Content-Type": "application/json"}, "response_too_large"),
    ],
)
async def test_public_feed_bounds_and_does_not_follow_redirects(status, content, headers, error):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, content=content, headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(SourceError, match=error):
            await PublicProjectFeedProvider(config(), client, 10).fetch()
        assert calls == 1


async def test_public_feed_timeout_and_no_guessed_verify_endpoint():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicProjectFeedProvider(config(), client, 10, timeout_seconds=0.01)
        with pytest.raises(SourceError, match="unsupported_verification"):
            await provider.verify("project-one")
        assert calls == 0
        with pytest.raises(TimeoutError):
            await provider.fetch()
        assert calls == 1
