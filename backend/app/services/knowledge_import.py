import asyncio
from datetime import datetime
from pathlib import Path

from pydantic import Field, TypeAdapter, model_validator

from app.core.config import get_settings
from app.core.db import Database
from app.domain.models import KnowledgeChunk
from app.domain.schemas import StrictModel


class KnowledgeInput(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1800)
    keywords: str = Field(min_length=1, max_length=500)
    source_url: str = Field(max_length=2000)
    reviewed_at: datetime
    expires_at: datetime
    demo: bool = False

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            not self.reviewed_at.tzinfo
            or not self.expires_at.tzinfo
            or self.expires_at <= self.reviewed_at
        ):
            raise ValueError("Knowledge needs review and expiry dates")
        if not self.source_url.startswith(("https://", "meken://guide/")):
            raise ValueError("Source must be HTTPS or a bundled Meken guide")
        return self


async def import_knowledge(db, path):
    rows = TypeAdapter(list[KnowledgeInput]).validate_json(await asyncio.to_thread(Path(path).read_text))
    if len({r.id for r in rows}) != len(rows):
        raise ValueError("Duplicate knowledge IDs")
    async with db.sessions.begin() as session:
        for row in rows:
            existing = await session.get(KnowledgeChunk, row.id)
            if existing:
                for key, value in row.model_dump().items():
                    setattr(existing, key, value)
            else:
                session.add(KnowledgeChunk(**row.model_dump()))
    return len(rows)


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", default="data/knowledge.json")
    args = parser.parse_args()
    db = Database(get_settings().database_url)
    try:
        print({"imported_knowledge": await import_knowledge(db, args.file)})
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
