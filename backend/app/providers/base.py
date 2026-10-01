from typing import Protocol

from app.domain.schemas import Snapshot


class SourceError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ApartmentProvider(Protocol):
    id: str
    name: str
    cities: list[str]
    demo: bool

    async def fetch(self, city: str | None = None) -> Snapshot: ...

    async def verify(self, external_id: str) -> Snapshot: ...
