import asyncio
import hashlib
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.domain.models import Conversation, Turn, utcnow
from app.domain.schemas import TurnRequest


async def test_creation_retries_use_immutable_normalized_snapshot(client, app):
    key = str(uuid4())
    request = {
        "client_conversation_id": key,
        "preferences": {"city": "astana", "rooms": [2, 2], "budget_max": 35_000_000},
    }
    created = await client.post("/v1/conversations", json=request)
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    await client.put(f"/v1/conversations/{conversation_id}/preferences", json={"city": "Алматы"})
    replay = await client.post(
        "/v1/conversations",
        json={**request, "preferences": {"city": "Астана", "rooms": [2], "budget_max": 35_000_000}},
    )
    assert replay.status_code == 201 and replay.json()["id"] == conversation_id
    assert replay.json()["preferences"]["city"] == "Алматы"
    conflict = await client.post(
        "/v1/conversations", json={**request, "preferences": {"city": "Алматы"}}
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"error": {"code": "idempotency_conflict"}}
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_parallel_creation_retries_create_one_conversation(client, app):
    body = {"client_conversation_id": str(uuid4()), "preferences": {"city": "Астана"}}
    responses = await asyncio.gather(*(client.post("/v1/conversations", json=body) for _ in range(6)))
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 1
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_parallel_creation_different_snapshot_conflicts(client, app):
    key = str(uuid4())
    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/conversations",
                json={"client_conversation_id": key, "preferences": {"city": city}},
            )
            for city in ["Астана", "Алматы"]
        )
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_creation_key_is_per_user_and_legacy_clients_remain_supported(client, app):
    body = {"client_conversation_id": str(uuid4()), "preferences": {"city": "Астана"}}
    first = (await client.post("/v1/conversations", json=body)).json()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        tokens = (await other.post("/v1/auth/anonymous")).json()
        other.headers["Authorization"] = "Bearer " + tokens["access_token"]
        second = (await other.post("/v1/conversations", json=body)).json()
        assert first["id"] != second["id"]
        assert (await other.get(f"/v1/conversations/{first['id']}")).status_code == 404
    legacy = [(await client.post("/v1/conversations", json={})).json() for _ in range(2)]
    assert legacy[0]["id"] != legacy[1]["id"]
    assert (await client.get(f"/v1/conversations/{legacy[0]['id']}")).status_code == 200


async def test_history_cursor_keeps_all_turns_with_tied_timestamps(client, app):
    conversation_id = (await client.post("/v1/conversations", json={})).json()["id"]
    timestamp = utcnow()
    turn_ids = sorted(str(uuid4()) for _ in range(65))
    async with app.state.db.sessions.begin() as db:
        db.add_all(
            Turn(
                id=turn_id,
                conversation_id=conversation_id,
                client_turn_id=str(uuid4()),
                message=str(index),
                status="complete",
                created_at=timestamp,
            )
            for index, turn_id in enumerate(turn_ids)
        )
    pages, before = [], None
    while True:
        response = await client.get(
            f"/v1/conversations/{conversation_id}", params={"before": before} if before else {}
        )
        assert response.status_code == 200
        value = response.json()
        pages = [turn["id"] for turn in value["turns"]] + pages
        if not value["has_more"]:
            break
        before = value["next_before"]
    assert pages == turn_ids


async def test_turn_retry_survives_another_request_finishing_before_lock(client, app, monkeypatch):
    conversation_id = (await client.post("/v1/conversations", json={})).json()["id"]
    user_id = (await client.get("/v1/me")).json()["id"]
    request = TurnRequest(client_turn_id=uuid4(), message="Хочу квартиру")
    saved_turn_id = str(uuid4())
    acquire = app.state.coordination.acquire

    async def other_request_finishes(key, ttl):
        async with app.state.db.sessions.begin() as db:
            db.add(
                Turn(
                    id=saved_turn_id,
                    conversation_id=conversation_id,
                    client_turn_id=str(request.client_turn_id),
                    message=request.message,
                    request_hash=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                    status="complete",
                )
            )
        return await acquire(key, ttl)

    monkeypatch.setattr(app.state.coordination, "acquire", other_request_finishes)
    turn_id, lease = await app.state.chat.reserve(conversation_id, user_id, request)
    assert turn_id == saved_turn_id and lease is None
    assert await app.state.coordination.get("conversation:" + conversation_id) is None
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Turn)) == 1


async def test_turn_start_and_delete_are_serialized(client, app, monkeypatch):
    conversation_id = (await client.post("/v1/conversations", json={})).json()["id"]
    user_id = (await client.get("/v1/me")).json()["id"]
    request = TurnRequest(client_turn_id=uuid4(), message="Хочу квартиру")
    coordination = app.state.coordination
    acquire = coordination.acquire
    token = await acquire("conversation:" + conversation_id, 180)
    assert (await client.delete(f"/v1/conversations/{conversation_id}")).status_code == 409
    await coordination.release("conversation:" + conversation_id, token)

    async def delete_finishes_before_acquire(key, ttl):
        async with app.state.db.sessions.begin() as db:
            await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        return await acquire(key, ttl)

    monkeypatch.setattr(coordination, "acquire", delete_finishes_before_acquire)
    with pytest.raises(HTTPException) as error:
        await app.state.chat.reserve(conversation_id, user_id, request)
    assert error.value.status_code == 404 and error.value.detail == "conversation_not_found"
    assert await coordination.get("conversation:" + conversation_id) is None
