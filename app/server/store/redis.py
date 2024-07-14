from typing import Literal

import redis.asyncio as aioredis
from pydantic import BaseModel

from .store import SimpleMapping, Store, StoreSession


class RedisConfig(BaseModel):
    engine: Literal["redis"] = "redis"
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"

    def driver(self) -> "RedisStore":
        return RedisStore(self)


class RedisStoreSession(StoreSession):
    def __init__(self, pool: aioredis.ConnectionPool):
        self.pool = pool
        self.client = aioredis.Redis(connection_pool=self.pool)
        self.pipe = self.client.pipeline(transaction=True)

    async def open(self):
        pass

    async def commit(self):
        await self.pipe.execute()

    async def rollback(self):
        await self.pipe.discard()

    async def close(self):
        await self.client.aclose(close_connection_pool=False)

    async def ping(self):
        return await self.client.ping()

    async def set(self, key: str, value: str):
        await self.pipe.set(key, value)

    async def get(self, key: str) -> bytes | None:
        await self.pipe.watch(key)
        return await self.client.get(key)

    async def sadd(self, key: str, *value):
        await self.pipe.sadd(key, *value)

    async def hsetmapping(self, key: str, mapping: SimpleMapping):
        await self.pipe.hset(key, mapping=mapping)

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return await self.client.hgetall(key)


class RedisStore(Store):
    def __init__(self, config: RedisConfig):
        self.config = config
        self.pool: aioredis.ConnectionPool | None = None

    async def init(self):
        self.pool = aioredis.ConnectionPool.from_url(self.config.url)

    async def close(self):
        await self.pool.aclose()

    def tx(self) -> RedisStoreSession:
        if self.pool is None:
            raise ValueError("Store is not initialized")
        return RedisStoreSession(self.pool)
