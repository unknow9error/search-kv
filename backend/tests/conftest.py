import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.core.db import Database
from app.main import create_app
from app.providers.demo import DemoProvider


@pytest.fixture
async def app(tmp_path):
    database_url = os.getenv("MEKEN_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/test.sqlite3"
    db = Database(database_url)
    schema = "test_" + uuid4().hex if database_url.startswith("postgresql") else None
    if schema:
        async with db.engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        db.engine = db.engine.execution_options(schema_translate_map={None: schema})
        db.sessions.configure(bind=db.engine)
    settings = Settings(
        env="demo",
        database_url=database_url,
        auto_create_schema=True,
        source_timeout_seconds=0.15,
    )
    providers = [DemoProvider("garden", 0.001), DemoProvider("river", 0.025)]
    app = create_app(settings, providers_override=providers, database=db)
    try:
        async with app.router.lifespan_context(app):
            yield app
    finally:
        if schema:
            async with db.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await db.engine.dispose()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        token = (await client.post("/v1/auth/anonymous")).json()
        client.headers["Authorization"] = "Bearer " + token["access_token"]
        yield client


@pytest.fixture
async def seeded(app):
    async for _ in app.state.catalog.refresh_events(None):
        pass
    return app.state.catalog
