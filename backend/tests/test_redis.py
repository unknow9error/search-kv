import asyncio
import os
from uuid import uuid4

import pytest

from app.core.coordination import Coordination


@pytest.mark.skipif(not os.getenv("MEKEN_TEST_REDIS_URL"), reason="Requires integration Redis")
async def test_real_redis_atomic_counters_and_lease_ownership():
    one = Coordination(os.environ["MEKEN_TEST_REDIS_URL"])
    two = Coordination(os.environ["MEKEN_TEST_REDIS_URL"])
    key = "integration:" + uuid4().hex
    try:
        results = await asyncio.gather(*[(one if x % 2 else two).increment(key, 60) for x in range(40)])
        assert sorted(results) == list(range(1, 41))
        lease = await one.acquire(key + ":lease", 30)
        assert lease is not None and await two.acquire(key + ":lease", 30) is None
        await two.release(key + ":lease", "wrong-owner")
        assert await one.get(key + ":lease") == lease
        await one.release(key + ":lease", lease)
        assert await two.acquire(key + ":lease", 1) is not None
    finally:
        await one.close()
        await two.close()
