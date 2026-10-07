"""Exercise a deployed HTTPS API with a temporary user and real provider data."""
import argparse
import asyncio
import json
import time
from uuid import uuid4

import httpx


async def check(base):
    async with httpx.AsyncClient(base_url=base, timeout=150, follow_redirects=False) as client:
        config_response = await client.get("/v1/config")
        config_response.raise_for_status()
        config = config_response.json()
        assert config["mode"] == "live"
        for path in ("/metrics", "/docs", "/openapi.json", "/health/ready"):
            assert (await client.get(path)).status_code == 404, path
        for name in ("privacy_url", "terms_url"):
            assert config[name].startswith(base + "/")
            assert (await client.get(config[name])).status_code == 200
        auth = await client.post("/v1/auth/anonymous")
        auth.raise_for_status()
        client.headers["Authorization"] = "Bearer " + auth.json()["access_token"]
        started = time.monotonic()
        cards, first_event, first_cards, done = {}, None, None, False
        verification = None
        try:
            conversation = await client.post("/v1/conversations", json={})
            conversation.raise_for_status()
            conversation_id = conversation.json()["id"]
            async with client.stream(
                "POST",
                f"/v1/conversations/{conversation_id}/turns",
                json={
                    "client_turn_id": str(uuid4()),
                    "message": "Хочу 2-комнатную квартиру в Астане до 35 млн",
                },
            ) as response:
                response.raise_for_status()
                assert "text/event-stream" in response.headers["content-type"]
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                        if first_event is None:
                            first_event = time.monotonic() - started
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if event == "listings" and data["items"]:
                            if first_cards is None:
                                first_cards = time.monotonic() - started
                            for item in data["items"]:
                                assert item["provenance"] == "provider"
                                assert item["city"] == "Астана"
                                assert item["rooms"] == 2
                                assert item["price_kzt"] <= 35_000_000
                                cards[item["id"]] = item
                        elif event == "done":
                            done = data["status"] == "complete"
            elapsed = time.monotonic() - started
            assert done and cards, "No complete turn with real provider cards"
            assert first_event < elapsed, "Events were not streamed"
            listing_id = next(iter(cards))
            detail = await client.get(f"/v1/apartments/{listing_id}")
            detail.raise_for_status()
            assert detail.json()["id"] == listing_id
            verified = await client.post(f"/v1/apartments/{listing_id}/verify")
            verified.raise_for_status()
            verification = verified.json()["verification"]
            assert (await client.put(f"/v1/favorites/{listing_id}")).status_code == 204
            favorites = await client.get("/v1/favorites")
            favorites.raise_for_status()
            assert any(x["id"] == listing_id for x in favorites.json()["items"])
        finally:
            deleted = await client.delete("/v1/me")
            assert deleted.status_code == 204, "Temporary user cleanup failed"
            assert (await client.get("/v1/me")).status_code == 401
        return {
            "endpoint": base,
            "mode": config["mode"],
            "ai_enabled": config["ai_enabled"],
            "provider_cards": len(cards),
            "first_event_seconds": round(first_event, 3),
            "first_cards_seconds": round(first_cards, 3),
            "turn_seconds": round(elapsed, 3),
            "turn_complete": done,
            "verification": verification,
            "favorites": "passed",
            "temporary_user": "deleted",
            "internal_endpoints": "blocked",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("--allow-live-write", action="store_true", required=True)
    args = parser.parse_args()
    if not args.base.startswith("https://"):
        parser.error("HTTPS endpoint required; certificate verification remains enabled")
    print(json.dumps(asyncio.run(check(args.base.rstrip("/"))), ensure_ascii=False, indent=2))
