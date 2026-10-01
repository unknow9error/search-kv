import asyncio
import time
from contextlib import asynccontextmanager
from secrets import token_hex

from redis.asyncio import Redis


class BusyError(Exception):
    pass


class Coordination:
    """Redis in production; bounded process-local implementation for demo/tests only."""

    def __init__(self, url: str):
        self.redis = (
            Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
            if url
            else None
        )
        self.values: dict[str, tuple[str, float]] = {}
        self.mutex = asyncio.Lock()

    def _prune(self):
        now = time.monotonic()
        self.values = {k: v for k, v in self.values.items() if v[1] > now}

    async def get(self, key: str) -> str | None:
        if self.redis:
            return await self.redis.get("meken:" + key)
        async with self.mutex:
            self._prune()
            return self.values.get(key, (None, 0))[0]

    async def set(self, key: str, value: str, ttl: int):
        if self.redis:
            await self.redis.set("meken:" + key, value, ex=ttl)
        else:
            async with self.mutex:
                self._prune()
                self.values[key] = (value, time.monotonic() + ttl)

    async def increment(self, key: str, ttl: int) -> int:
        if self.redis:
            return int(
                await self.redis.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1,
                    "meken:" + key,
                    ttl,
                )
            )
        async with self.mutex:
            self._prune()
            old, expiry = self.values.get(key, ("0", time.monotonic() + ttl))
            value = int(old) + 1
            self.values[key] = (str(value), expiry)
            return value

    async def acquire(self, key: str, ttl: int) -> str | None:
        token = token_hex(16)
        if self.redis:
            return token if await self.redis.set("meken:" + key, token, nx=True, ex=ttl) else None
        async with self.mutex:
            self._prune()
            if key in self.values:
                return None
            self.values[key] = (token, time.monotonic() + ttl)
            return token

    async def release(self, key: str, token: str):
        if self.redis:
            await self.redis.eval(
                "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",
                1,
                "meken:" + key,
                token,
            )
        else:
            async with self.mutex:
                if self.values.get(key, (None,))[0] == token:
                    self.values.pop(key)

    @asynccontextmanager
    async def lease(self, key: str, ttl: int):
        token = await self.acquire(key, ttl)
        if token is None:
            raise BusyError(key)
        try:
            yield token
        finally:
            await asyncio.shield(self.release(key, token))

    async def close(self):
        if self.redis:
            await self.redis.aclose()
