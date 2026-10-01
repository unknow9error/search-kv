import asyncio
import random
import signal
from datetime import timedelta

from sqlalchemy import delete, exists, select, update

from app.core.coordination import BusyError
from app.domain.models import Conversation, ImportRun, Session, Turn, User, utcnow
from app.main import create_app


async def retention(db, days):
    async with db.sessions.begin() as session:
        await session.execute(delete(Session).where(Session.refresh_expires_at < utcnow()))
        await session.execute(
            delete(Conversation).where(Conversation.updated_at < utcnow() - timedelta(days=days))
        )
        await session.execute(
            delete(ImportRun).where(ImportRun.started_at < utcnow() - timedelta(days=30))
        )
        await session.execute(
            update(Turn)
            .where(Turn.status == "running", Turn.created_at < utcnow() - timedelta(seconds=180))
            .values(status="interrupted")
        )
        await session.execute(
            delete(User).where(
                User.created_at < utcnow() - timedelta(days=days),
                ~exists(select(Session.id).where(Session.user_id == User.id)),
            )
        )


async def run(once=False):
    app = create_app()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    async with app.router.lifespan_context(app):
        while not stop.is_set():
            try:
                async with app.state.coordination.lease("worker-cycle", 180):
                    async for _ in app.state.catalog.refresh_events(None):
                        pass
                    await retention(app.state.db, app.state.settings.retention_days)
            except BusyError:
                pass
            if once:
                return
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=app.state.settings.refresh_interval_seconds + random.uniform(0, 15),
                )
            except TimeoutError:
                pass


if __name__ == "__main__":
    import sys

    asyncio.run(run(once="--once" in sys.argv))
