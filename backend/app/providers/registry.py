from pathlib import Path

import httpx
from sqlalchemy import update

from app.core.config import Settings
from app.core.db import Database
from app.domain.models import Provider
from app.providers.base import ApartmentProvider
from app.providers.bi_group import BIGroupProvider
from app.providers.demo import DemoProvider
from app.providers.json_feed import JsonFeedProvider, config_adapter
from app.providers.public_project_feed import PublicProjectFeedProvider


def build_providers(
    settings: Settings, client: httpx.AsyncClient, coordination
) -> list[ApartmentProvider]:
    if settings.env == "demo":
        return [DemoProvider("garden", 0.3), DemoProvider("river", 1.0)]
    configs = config_adapter.validate_json(Path(settings.provider_config).read_text())
    if len({c.id for c in configs}) != len(configs):
        raise ValueError("Provider IDs must be unique")
    providers = []
    for config in configs:
        if config.enabled:
            if config.kind == "bi_group":
                providers.append(BIGroupProvider(client, coordination, config.cities))
                continue
            if config.kind == "public_project_feed":
                providers.append(
                    PublicProjectFeedProvider(
                        config, client, settings.max_snapshot_items, settings.source_timeout_seconds
                    )
                )
                continue
            if config.kind != "json_feed":
                raise ValueError("Unknown provider kind: " + config.kind)
            providers.append(JsonFeedProvider(config, client, settings.max_snapshot_items))
    if settings.env == "production" and not providers:
        raise ValueError("Production requires at least one configured real provider")
    return providers


async def sync_registry(db: Database, providers: list[ApartmentProvider]):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    async with db.sessions.begin() as session:
        await session.execute(update(Provider).values(enabled=False))
        for provider in providers:
            statement = insert(Provider).values(
                id=provider.id, name=provider.name, demo=provider.demo, enabled=True
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Provider.id],
                    set_={"name": provider.name, "demo": provider.demo, "enabled": True},
                )
            )
