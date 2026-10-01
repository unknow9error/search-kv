"""Versioned, source-linked developer facts at complex/bigville scope.

Never infer operating status from marketing text or an announced future opening.
The importer accepts structured partner data or an explicitly reviewed document extraction.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import Database
from app.domain.models import ContextDocument, ProjectFact, Provider, aware
from app.domain.schemas import ProjectContextInput


async def ingest_context(db: Database, context: ProjectContextInput):
    async with db.sessions.begin() as session:
        provider = await session.scalar(
            select(Provider).where(Provider.id == context.provider_id).with_for_update()
        )
        if not provider:
            raise ValueError("Register the provider before importing its context")
        scope = (
            ProjectFact.provider_id == context.provider_id,
            ProjectFact.scope_type == context.scope_type,
            ProjectFact.scope_id == context.scope_id,
            ProjectFact.source_url == str(context.source_url),
        )
        document_id = hashlib.sha256(
            f"{context.provider_id}:{context.scope_type}:{context.scope_id}:{context.source_url}".encode()
        ).hexdigest()
        content_hash = hashlib.sha256(
            json.dumps([f.model_dump() for f in context.facts], sort_keys=True).encode()
        ).hexdigest()
        document = await session.get(ContextDocument, document_id)
        if document and aware(document.observed_at) >= context.observed_at:
            if (
                aware(document.observed_at) == context.observed_at
                and document.content_hash != content_hash
            ):
                raise ValueError("Different document content has the same observation version")
            return 0
        if document:
            document.observed_at, document.content_hash = context.observed_at, content_hash
        else:
            session.add(
                ContextDocument(
                    id=document_id,
                    provider_id=context.provider_id,
                    observed_at=context.observed_at,
                    content_hash=content_hash,
                )
            )
        await session.execute(delete(ProjectFact).where(*scope))
        for fact in context.facts:
            session.add(
                ProjectFact(
                    provider_id=context.provider_id,
                    scope_type=context.scope_type,
                    scope_id=context.scope_id,
                    source_url=str(context.source_url),
                    observed_at=context.observed_at,
                    expires_at=context.expires_at,
                    **fact.model_dump(),
                )
            )
    return len(context.facts)


async def main():
    import argparse

    from app.core.config import get_settings

    parser = argparse.ArgumentParser(description="Import reviewed developer project/bigville context")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    context = ProjectContextInput.model_validate_json(args.file.read_text())
    db = Database(get_settings().database_url)
    try:
        print({"imported": await ingest_context(db, context)})
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
