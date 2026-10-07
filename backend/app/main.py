import asyncio
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response

from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.recovery import router as recovery_router
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.coordination import Coordination
from app.core.db import Database
from app.core.telemetry import HTTP_LATENCY, configure_logging
from app.providers.registry import build_providers, sync_registry
from app.services.assistant import Assistant
from app.services.catalog import Catalog
from app.services.chat import Chat
from app.services.project_conversations import ProjectConversations
from app.services.projects import Projects, bootstrap_legacy_projects


class BodyLimitMiddleware:
    def __init__(self, app, limit=16384):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                return await JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)(
                    scope, receive, send
                )
            chunks.append(message)
            if not message.get("more_body"):
                break

        async def replay_receive():
            if chunks:
                return chunks.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


def migration_heads():
    from pathlib import Path

    from alembic.script import ScriptDirectory

    return set(ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations")).get_heads())


def bundled_contexts():
    from pathlib import Path

    from app.domain.schemas import ProjectContextInput

    folder = Path(__file__).resolve().parents[1] / "data" / "contexts"
    return [
        ProjectContextInput.model_validate_json(path.read_text())
        for path in sorted(folder.glob("*.json"))
    ]


def create_app(
    settings: Settings | None = None, providers_override=None, database: Database | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        db = database or Database(settings.database_url)
        coordination = Coordination(settings.redis_url)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.source_timeout_seconds),
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
            follow_redirects=False,
            trust_env=False,
        )
        assistant = Assistant(settings, coordination, db)
        app.state.settings, app.state.db, app.state.coordination = settings, db, coordination
        try:
            if settings.auto_create_schema:
                await db.create_schema()
            if settings.env == "production":
                expected = await asyncio.to_thread(migration_heads)
                async with db.sessions() as session:
                    current = set(
                        (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
                    )
                    role = (
                        await session.execute(
                            text(
                                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
                            )
                        )
                    ).one()
                    if any(role):
                        raise RuntimeError(
                            "Production API requires a restricted database role, separate from migrations"
                        )
                if current != expected:
                    raise RuntimeError("Apply all database migrations before production startup")
            if coordination.redis:
                await coordination.redis.ping()
            providers = (
                providers_override
                if providers_override is not None
                else build_providers(settings, client, coordination)
            )
            await sync_registry(db, providers)
            if settings.env != "demo":
                from app.services.project_context import ingest_context

                for context in await asyncio.to_thread(bundled_contexts):
                    if context.provider_id in {p.id for p in providers}:
                        await ingest_context(db, context)
            catalog = Catalog(db, coordination, providers, settings)
            app.state.catalog, app.state.assistant = catalog, assistant
            app.state.chat = Chat(db, coordination, catalog, assistant)
            await bootstrap_legacy_projects(db, catalog, settings)
            app.state.projects = Projects(db, coordination, catalog, settings)
            app.state.project_chat = ProjectConversations(db, coordination, app.state.projects, settings)
            yield
        finally:
            await assistant.close()
            await client.aclose()
            await coordination.close()
            await db.engine.dispose()

    app = FastAPI(
        title="Meken API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.env == "production" else "/docs",
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env != "production" else None,
    )
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def headers_and_metrics(request: Request, call_next):
        request_id, start = secrets.token_hex(12), time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        if settings.env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_LATENCY.labels(request.method, route, response.status_code).observe(
            time.monotonic() - start
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exception):
        return JSONResponse(
            {"error": {"code": str(exception.detail)}},
            status_code=exception.status_code,
            headers=exception.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exception):
        # Never echo the input (it can contain tokens or personal chat text).
        return JSONResponse({"error": {"code": "invalid_request"}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, exception):
        return JSONResponse({"error": {"code": "temporarily_unavailable"}}, status_code=503)

    @app.get("/health/live", tags=["operations"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["operations"])
    async def ready():
        try:
            async with asyncio.timeout(3):
                async with app.state.db.sessions() as db:
                    await db.execute(text("SELECT 1"))
                if app.state.coordination.redis:
                    await app.state.coordination.redis.ping()
            return {"status": "ready"}
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        expected = settings.metrics_token.get_secret_value()
        if not expected or not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + expected
        ):
            raise HTTPException(404, "not_found")
        return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    app.include_router(auth_router)
    app.include_router(recovery_router)
    app.include_router(router)
    app.include_router(projects_router)
    return app


app = create_app()
