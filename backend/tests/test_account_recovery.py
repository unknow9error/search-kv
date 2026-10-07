import asyncio
import hashlib
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update

from app.domain.models import Conversation, Favorite, Session, User, utcnow
from app.domain.recovery import RecoveryCredential
from app.domain.schemas import Preferences
from app.worker import retention


def authorization(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


async def test_recovery_uses_same_account_with_independent_device_sessions(client, app, seeded):
    original_id = (await client.get("/v1/me")).json()["id"]
    apartment = (await seeded.search(Preferences()))[0]
    await client.put(f"/v1/favorites/{apartment.id}")
    conversation = (await client.post("/v1/conversations", json={})).json()["id"]
    issued = await client.post("/v1/auth/recovery-code")
    assert issued.status_code == 200
    assert issued.headers["Cache-Control"] == "no-store"
    code = issued.json()["recovery_code"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", code)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as device:
        previous = (await device.post("/v1/auth/anonymous")).json()
        previous_conversation = (
            await device.post("/v1/conversations", json={}, headers=authorization(previous))
        ).json()["id"]
        recovered = await device.post("/v1/auth/recover", json={"recovery_code": code})
        assert recovered.status_code == 200
        assert recovered.headers["Cache-Control"] == "no-store"
        tokens = recovered.json()
        assert tokens["user_id"] == original_id != previous["user_id"]
        device.headers.update(authorization(tokens))
        assert (await device.get("/v1/me")).json()["id"] == original_id
        assert (await device.get("/v1/favorites")).json()["items"][0]["id"] == apartment.id
        assert (await device.get(f"/v1/conversations/{conversation}")).status_code == 200
        # Recovery never merges the identity or data from the device's prior account.
        assert (await device.get(f"/v1/conversations/{previous_conversation}")).status_code == 404
        assert (
            await device.get(
                f"/v1/conversations/{previous_conversation}", headers=authorization(previous)
            )
        ).status_code == 200

        refreshed = await device.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == 200
        # Rotating one recovered device's tokens does not invalidate another device.
        assert (await client.get("/v1/me")).status_code == 200
        again = await device.post("/v1/auth/recover", json={"recovery_code": code})
        assert again.status_code == 200 and again.json()["user_id"] == original_id
        assert again.json()["refresh_token"] != tokens["refresh_token"]
    async with app.state.db.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Session).where(Session.user_id == original_id)
            )
            == 3
        )


async def test_recovery_survives_expired_device_refresh_token(client, app):
    tokens = (await client.post("/v1/auth/anonymous")).json()
    client.headers.update(authorization(tokens))
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    async with app.state.db.sessions.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.user_id == tokens["user_id"])
            .values(
                access_expires_at=utcnow() - timedelta(hours=1),
                refresh_expires_at=utcnow() - timedelta(days=1),
            )
        )
    assert (await client.get("/v1/me")).status_code == 401
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
    recovered = await client.post("/v1/auth/recover", json={"recovery_code": code})
    assert recovered.status_code == 200 and recovered.json()["user_id"] == tokens["user_id"]


async def test_rotation_revokes_previous_code_and_never_stores_or_logs_plaintext(client, app, caplog):
    user_id = (await client.get("/v1/me")).json()["id"]
    old = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    new = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    assert old != new
    invalid = await client.post("/v1/auth/recover", json={"recovery_code": old})
    unknown = await client.post("/v1/auth/recover", json={"recovery_code": "unknown"})
    assert invalid.status_code == unknown.status_code == 401
    assert invalid.json() == unknown.json() == {"error": {"code": "invalid_recovery_code"}}
    recovered = await client.post("/v1/auth/recover", json={"recovery_code": "  " + new + "\n"})
    assert recovered.status_code == 200 and recovered.json()["user_id"] == user_id
    async with app.state.db.sessions() as db:
        stored = await db.scalar(select(RecoveryCredential))
        assert stored.user_id == user_id
        assert stored.code_hash == hashlib.sha256(new.encode()).hexdigest()
        assert old not in stored.code_hash and new not in stored.code_hash
        assert await db.scalar(select(func.count()).select_from(RecoveryCredential)) == 1
    assert old not in caplog.text and new not in caplog.text
    assert (await client.get("/v1/me")).status_code == 200


async def test_delete_account_cascades_recovery_and_all_device_sessions(client, app, seeded):
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    tokens = (await client.post("/v1/auth/recover", json={"recovery_code": code})).json()
    apartment = (await seeded.search(Preferences()))[0]
    await client.put(f"/v1/favorites/{apartment.id}")
    await client.post("/v1/conversations", json={})
    assert (await client.delete("/v1/me")).status_code == 204
    assert (await client.get("/v1/me", headers=authorization(tokens))).status_code == 401
    invalid = await client.post("/v1/auth/recover", json={"recovery_code": code})
    assert invalid.status_code == 401 and invalid.json()["error"]["code"] == "invalid_recovery_code"
    async with app.state.db.sessions() as db:
        assert await db.get(User, user_id) is None
        for model in [RecoveryCredential, Session, Conversation, Favorite]:
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_recovery_issuance_requires_authentication_and_is_rate_limited(client):
    response = await client.post("/v1/auth/recovery-code", headers={"Authorization": ""})
    assert response.status_code == 401
    for _ in range(5):
        assert (await client.post("/v1/auth/recovery-code")).status_code == 200
    limited = await client.post("/v1/auth/recovery-code")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["Retry-After"] == "3600"


async def test_recovery_code_rate_limit_applies_across_ip_addresses(app):
    for attempt in range(6):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(f"10.0.0.{attempt + 1}", 123)),
            base_url="http://testserver",
        ) as device:
            response = await device.post("/v1/auth/recover", json={"recovery_code": "unknown"})
        assert response.status_code == (401 if attempt < 5 else 429)
    assert response.headers["Retry-After"] == "60"


async def test_recovery_ip_rate_limit_applies_to_different_codes(client):
    for attempt in range(11):
        response = await client.post("/v1/auth/recover", json={"recovery_code": str(uuid4())})
        assert response.status_code == (401 if attempt < 10 else 429)
    assert response.headers["Retry-After"] == "60"


async def test_recovery_validation_does_not_echo_secret(client):
    secret = "private-recovery-code" * 10
    invalid = await client.post("/v1/auth/recover", json={"recovery_code": secret})
    assert invalid.status_code == 422
    assert secret not in invalid.text and "private-recovery-code" not in invalid.text


async def test_concurrent_initial_issuance_leaves_only_one_valid_code(client, app):
    issued = await asyncio.gather(*(client.post("/v1/auth/recovery-code") for _ in range(3)))
    assert all(response.status_code == 200 for response in issued)
    results = await asyncio.gather(
        *(client.post("/v1/auth/recover", json=response.json()) for response in issued)
    )
    assert sorted(response.status_code for response in results) == [200, 401, 401]
    async with app.state.db.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(RecoveryCredential)) == 1


async def test_postgres_recovery_and_rotation_wait_without_lock_order_deadlock(client, app):
    if app.state.db.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock concurrency check")
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    query_started = asyncio.Queue()

    def record_lock(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            query_started.put_nowait(statement)

    # Queue the rotation before recovery behind a held account lock. An inverted
    # credential→user lock order lets recovery take the credential while rotation
    # owns the account, producing a real PostgreSQL deadlock after this release.
    async with app.state.db.sessions.begin() as db:
        await db.scalar(select(User).where(User.id == user_id).with_for_update())
        event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", record_lock)
        rotate = asyncio.create_task(client.post("/v1/auth/recovery-code"))
        recover = None
        try:
            await asyncio.wait_for(query_started.get(), timeout=2)
            recover = asyncio.create_task(client.post("/v1/auth/recover", json={"recovery_code": code}))
            await asyncio.wait_for(query_started.get(), timeout=2)
        finally:
            event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", record_lock)
    assert recover is not None
    rotated, recovered = await asyncio.wait_for(asyncio.gather(rotate, recover), timeout=5)
    assert rotated.status_code == 200
    assert recovered.status_code in {200, 401}
    assert (await client.post("/v1/auth/recover", json={"recovery_code": code})).status_code == 401
    assert (await client.post("/v1/auth/recover", json=rotated.json())).status_code == 200


async def age_account(app, user_id, code_age_days, expire_sessions=True):
    async with app.state.db.sessions.begin() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(created_at=utcnow() - timedelta(days=180))
        )
        await db.execute(
            update(RecoveryCredential)
            .where(RecoveryCredential.user_id == user_id)
            .values(updated_at=utcnow() - timedelta(days=code_age_days))
        )
        if expire_sessions:
            await db.execute(
                update(Session)
                .where(Session.user_id == user_id)
                .values(refresh_expires_at=utcnow() - timedelta(days=1))
            )


async def test_retention_preserves_recent_recovery_code_after_device_sessions_expire(client, app):
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    await age_account(app, user_id, code_age_days=89)
    await retention(app.state.db, 90)
    async with app.state.db.sessions() as db:
        assert await db.get(User, user_id) is not None
        assert await db.scalar(select(func.count()).select_from(Session)) == 0
    recovered = await client.post("/v1/auth/recover", json={"recovery_code": code})
    assert recovered.status_code == 200 and recovered.json()["user_id"] == user_id


async def test_retention_purges_unused_old_recovery_code_and_user_data(client, app, seeded):
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    apartment = (await seeded.search(Preferences()))[0]
    await client.put(f"/v1/favorites/{apartment.id}")
    await client.post("/v1/conversations", json={})
    await age_account(app, user_id, code_age_days=91)
    await retention(app.state.db, 90)
    async with app.state.db.sessions() as db:
        assert await db.get(User, user_id) is None
        for model in [RecoveryCredential, Session, Conversation, Favorite]:
            assert await db.scalar(select(func.count()).select_from(model)) == 0
    assert (await client.post("/v1/auth/recover", json={"recovery_code": code})).status_code == 401


async def test_successful_recovery_renews_retention_window_without_preserving_old_sessions(client, app):
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    await age_account(app, user_id, code_age_days=89)
    before = utcnow()
    recovered = await client.post("/v1/auth/recover", json={"recovery_code": code})
    assert recovered.status_code == 200
    # A later expired session does not cut the refreshed 90-day recovery window
    # back to the device refresh token's 30-day lifetime.
    async with app.state.db.sessions.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.user_id == user_id)
            .values(refresh_expires_at=utcnow() - timedelta(days=1))
        )
    await retention(app.state.db, 90)
    async with app.state.db.sessions() as db:
        credential = await db.get(RecoveryCredential, user_id)
        assert credential is not None
        timestamp = credential.updated_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=before.tzinfo)
        assert timestamp >= before
        assert await db.get(User, user_id) is not None
        assert await db.scalar(select(func.count()).select_from(Session)) == 0


async def test_refresh_renews_recovery_account_retention_window(client, app):
    tokens = (await client.post("/v1/auth/anonymous")).json()
    client.headers.update(authorization(tokens))
    await client.post("/v1/auth/recovery-code")
    await age_account(app, tokens["user_id"], code_age_days=91, expire_sessions=False)
    refreshed = await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    async with app.state.db.sessions.begin() as db:
        await db.execute(
            update(Session)
            .where(Session.user_id == tokens["user_id"])
            .values(refresh_expires_at=utcnow() - timedelta(days=1))
        )
    await retention(app.state.db, 90)
    async with app.state.db.sessions() as db:
        assert await db.get(User, tokens["user_id"]) is not None
        assert await db.get(RecoveryCredential, tokens["user_id"]) is not None


async def test_postgres_retention_skips_account_with_ongoing_recovery_lock(client, app):
    if app.state.db.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL skip-locked retention check")
    user_id = (await client.get("/v1/me")).json()["id"]
    code = (await client.post("/v1/auth/recovery-code")).json()["recovery_code"]
    await age_account(app, user_id, code_age_days=91)
    async with app.state.db.sessions.begin() as db:
        await db.scalar(select(User).where(User.id == user_id).with_for_update())
        await asyncio.wait_for(retention(app.state.db, 90), timeout=2)
        assert await db.get(User, user_id) is not None
    recovered = await client.post("/v1/auth/recover", json={"recovery_code": code})
    assert recovered.status_code == 200
    await retention(app.state.db, 90)
    async with app.state.db.sessions() as db:
        assert await db.get(User, user_id) is not None
