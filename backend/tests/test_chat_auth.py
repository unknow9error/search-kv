import asyncio
import json
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.domain.models import Conversation, Favorite, Session, Turn, TurnEvent
from app.domain.schemas import Preferences
from app.services.assistant import rules_plan


def parse_events(text):
    result = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            result.append((fields.get("event"), json.loads(fields["data"])))
    return result


async def test_beginner_gets_two_questions_not_invented_budget(client):
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    result = await client.post(
        f"/v1/conversations/{conversation}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Хочу квартиру"},
    )
    events = parse_events(result.text)
    assert events[0][0] == "accepted" and events[-1][0] == "done"
    prefs = next(payload for kind, payload in events if kind == "preferences")
    assert prefs["city"] is None and prefs["budget_max"] is None
    assert not any(kind == "provider" for kind, _ in events)
    assert "городе" in next(p["text"] for k, p in events if k == "message")


async def test_sse_search_and_replay_do_not_repeat_work(client, app, monkeypatch):
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    body = {"client_turn_id": str(uuid4()), "message": "Хочу квартиру в Астане до 35 млн"}
    result = await client.post(f"/v1/conversations/{conversation}/turns", json=body)
    events = parse_events(result.text)
    assert any(k == "listings" and p["items"] for k, p in events)
    assert next(p for k, p in events if k == "preferences")["city"] == "Астана"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Replay repeated a model call")

    monkeypatch.setattr(app.state.assistant, "plan", forbidden)
    replay = await client.post(f"/v1/conversations/{conversation}/turns", json=body)
    assert replay.text == result.text
    conflict = await client.post(
        f"/v1/conversations/{conversation}/turns", json={**body, "message": "different"}
    )
    assert conflict.status_code == 409


async def test_another_user_cannot_read_write_or_replay_chat(client, app):
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        token = (await other.post("/v1/auth/anonymous")).json()
        other.headers["Authorization"] = "Bearer " + token["access_token"]
        assert (await other.get(f"/v1/conversations/{conversation}")).status_code == 404
        assert (
            await other.put(f"/v1/conversations/{conversation}/preferences", json={"city": "Алматы"})
        ).status_code == 404
        assert (
            await other.post(
                f"/v1/conversations/{conversation}/turns",
                json={"client_turn_id": str(uuid4()), "message": "read"},
            )
        ).status_code == 404
        assert (await other.delete(f"/v1/conversations/{conversation}")).status_code == 404


async def test_refresh_rotates_and_old_access_is_rejected(client):
    tokens = (await client.post("/v1/auth/anonymous")).json()
    refreshed = await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
    assert (
        await client.get("/v1/me", headers={"Authorization": "Bearer " + tokens["access_token"]})
    ).status_code == 401
    assert (
        await client.get(
            "/v1/me", headers={"Authorization": "Bearer " + refreshed.json()["access_token"]}
        )
    ).status_code == 200


async def test_parallel_refresh_consumes_token_only_once(client):
    tokens = (await client.post("/v1/auth/anonymous")).json()
    results = await asyncio.gather(
        *(
            client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
            for _ in range(2)
        )
    )
    assert sorted(result.status_code for result in results) == [200, 401]
    successful = next(result.json() for result in results if result.status_code == 200)
    assert successful["user_id"] == tokens["user_id"]
    assert (
        await client.get("/v1/me", headers={"Authorization": "Bearer " + successful["access_token"]})
    ).status_code == 200
    assert (
        await client.get("/v1/me", headers={"Authorization": "Bearer " + tokens["access_token"]})
    ).status_code == 401


async def test_delete_data_revokes_token_and_cascades(client, app, seeded):
    apartment = (await seeded.search(Preferences()))[0]
    await client.put(f"/v1/favorites/{apartment.id}")
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    await client.post(
        f"/v1/conversations/{conversation}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Хочу квартиру"},
    )
    assert (await client.delete("/v1/me")).status_code == 204
    assert (await client.get("/v1/me")).status_code == 401
    async with app.state.db.sessions() as db:
        for model in [Session, Conversation, Turn, TurnEvent, Favorite]:
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_favorites_put_and_delete_idempotent(client, seeded):
    listing = (await seeded.search(Preferences()))[0]
    for _ in range(2):
        assert (await client.put("/v1/favorites/" + listing.id)).status_code == 204
    assert len((await client.get("/v1/favorites")).json()["items"]) == 1
    for _ in range(2):
        assert (await client.delete("/v1/favorites/" + listing.id)).status_code == 204


async def test_no_raw_input_in_validation_errors(client):
    response = await client.post("/v1/auth/refresh", json={"refresh_token": "secret"})
    assert response.status_code == 422 and "secret" not in response.text
    response = await client.post("/v1/conversations", content=b"x" * 20000)
    assert response.status_code == 413


async def test_knowledge_without_evidence_does_not_invent_answer(client):
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    result = await client.post(
        f"/v1/conversations/{conversation}/turns",
        json={"client_turn_id": str(uuid4()), "message": "Что такое ипотека?"},
    )
    message = next(p for k, p in parse_events(result.text) if k == "message")
    assert not message["citations"]
    assert "нет подтверждённого" in message["text"]


def test_budget_payment_not_total_and_prior_preferences_survive():
    previous = Preferences(city="Астана", budget_max=40_000_000, rooms=[2])
    result = rules_plan("Взнос 10 млн, школа рядом", previous, False)
    assert result.action == "clarify" and result.reason == "budget_ambiguous"
    assert result.preferences.budget_max == 40_000_000
    assert result.preferences.rooms == [2]


def test_negative_school_is_not_positive_filter():
    result = rules_plan("Школа не нужна", Preferences(preferred_amenities=["school"]), False)
    assert "school" not in result.preferences.preferred_amenities


async def test_partial_results_keep_reference_context_on_disconnect(app, client, seeded):
    from app.domain.schemas import TurnRequest

    user_id = (await client.get("/v1/me")).json()["id"]
    conversation = (
        await client.post("/v1/conversations", json={"preferences": {"city": "Астана"}})
    ).json()["id"]
    body = TurnRequest(client_turn_id=uuid4(), message="Покажи квартиры по моим условиям")
    turn_id, lease = await app.state.chat.reserve(conversation, user_id, body)
    stream = app.state.chat.stream(conversation, user_id, body, turn_id, lease)
    first_ids = []
    async for encoded in stream:
        events = parse_events(encoded)
        if events and events[0][0] == "listings" and events[0][1]["items"]:
            first_ids = [x["id"] for x in events[0][1]["items"]]
            break
    await stream.aclose()
    saved = await app.state.chat.conversation(conversation, user_id)
    assert first_ids and saved.last_listing_ids == first_ids
    async with app.state.db.sessions() as db:
        turn = await db.get(Turn, turn_id)
        assert turn.status == "interrupted"
