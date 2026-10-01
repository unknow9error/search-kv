"""Loopback HTTP smoke: observes actual streaming without printing bearer tokens."""
import asyncio
import json
import sys
import time
from uuid import uuid4

import httpx


async def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    if not base.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("This smoke is restricted to local services")
    async with httpx.AsyncClient(base_url=base, timeout=120) as client:
        assert (await client.get("/health/ready")).status_code == 200
        auth = await client.post("/v1/auth/anonymous")
        auth.raise_for_status()
        client.headers["Authorization"] = "Bearer " + auth.json()["access_token"]
        conversation = (await client.post("/v1/conversations", json={})).json()["id"]
        started, first_event, first_cards, count, done = time.monotonic(), None, None, 0, False
        async with client.stream("POST", f"/v1/conversations/{conversation}/turns", json={"client_turn_id": str(uuid4()), "message": "Хочу 2-комнатную квартиру в Астане до 35 млн"}) as response:
            response.raise_for_status()
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                    first_event = first_event if first_event is not None else time.monotonic() - started
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event == "listings" and data["items"]:
                        first_cards = first_cards if first_cards is not None else time.monotonic() - started
                        count = len(data["items"])
                        assert all(x["city"] == "Астана" and x["rooms"] == 2 and x["price_kzt"] <= 35_000_000 for x in data["items"])
                    if event == "done":
                        done = data["status"] == "complete"
        assert done and count > 0
        elapsed = time.monotonic()-started
        assert first_event < elapsed
        print(json.dumps({"first_event_seconds": round(first_event,3), "first_cards_seconds": round(first_cards,3), "total_seconds": round(elapsed,3), "cards": count, "complete": done}))


if __name__ == "__main__":
    asyncio.run(main())
