"""Explicit, read-only BI Group integration smoke. No user credentials or LLM calls."""
import asyncio

from app.core.config import Settings
from app.domain.schemas import Preferences
from app.main import create_app


async def main():
    settings = Settings(env="development", database_url="sqlite+aiosqlite:///./live.sqlite3", auto_create_schema=True, provider_config="data/providers.bi-group.json")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        prefs = Preferences(city="Астана", budget_max=35_000_000, rooms=[2])
        async for event in app.state.catalog.refresh_events("Астана", prefs):
            print(event, flush=True)
        results = await app.state.catalog.search(prefs)
        assert results, "No live apartments found"
        assert all(x.city == "Астана" and x.rooms == 2 and x.price_kzt <= 35_000_000 and x.provenance == "provider" for x in results)
        verified = await app.state.catalog.verify(results[0].id)
        print({"count": len(results), "sample": results[0].complex_name, "price": results[0].price_kzt, "verification": verified["verification"]}, flush=True)
        assert verified["listing"]["id"] == results[0].id


if __name__ == "__main__":
    asyncio.run(main())
