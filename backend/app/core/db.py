from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        options = {}
        if url.startswith("postgresql"):
            options = {
                "pool_timeout": 5,
                "connect_args": {
                    "command_timeout": 15,
                    "server_settings": {
                        "statement_timeout": "10000",
                        "lock_timeout": "3000",
                        "idle_in_transaction_session_timeout": "15000",
                    },
                },
            }
        self.engine = create_async_engine(url, pool_pre_ping=True, **options)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def configure_sqlite(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self):
        from app.domain import models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
