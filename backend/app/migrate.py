import asyncio
import subprocess
import sys

from app.core.config import get_settings
from app.core.db import Database
from app.services.knowledge_import import import_knowledge


async def seed():
    db = Database(get_settings().database_url)
    try:
        await import_knowledge(db, "data/knowledge.json")
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    asyncio.run(seed())
