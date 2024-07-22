from typing import Literal

import redis.asyncio as aioredis
from pydantic import BaseModel

from .store import SimpleMapping, Store, StoreSession


class RedisConfig(BaseModel):
    engine: Literal["redis"] = "redis"
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ssl: bool = False
    password: str = ""
    user: str = ""

    @property
    def url(self) -> str:
        tls = ""
        q = ""
        if self.ssl:
            tls = "s"
            q = "?ssl_cert_reqs=none"
        auth = ""
        if self.user or self.password:
            auth = f"{self.user}:{self.password}@"

        return f"redis{tls}://{auth}{self.host}:{self.port}/{self.db}{q}"

    def driver(self) -> "RedisStore":
        return RedisStore(self)


class RedisTestConfig(BaseModel):
    engine: Literal["test-redis"] = "test-redis"
    url: str = "redis://localhost:6379/0"

    def driver(self) -> "TestRedisStore":
        return TestRedisStore(self)


class BaseRedisStoreSession(StoreSession):
    pipe: aioredis.client.Pipeline
    client: aioredis.Redis

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

    async def expire_at(self, key: str, expire_at: int):
        await self.pipe.expireat(key, expire_at)

    async def expire_time(self, key: str) -> int:
        return await self.pipe.expiretime(key)  # type: ignore

    async def ttl(self, key: str, ttl: int):
        await self.pipe.expire(key, ttl)


class RedisStoreSession(BaseRedisStoreSession):
    def __init__(self, pool: aioredis.ConnectionPool):
        self.pool = pool
        self.client = aioredis.Redis(connection_pool=self.pool)
        self.pipe = self.client.pipeline(transaction=True)


class TestRedisStoreSession(BaseRedisStoreSession):
    def __init__(self, client):
        self.client = client
        self.pipe = self.client.pipeline(transaction=True)


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


class TestRedisStore(Store):
    def __init__(self, config: RedisTestConfig):
        self.config = config

    async def init(self):
        from fakeredis import FakeAsyncRedis

        self.client = FakeAsyncRedis()

    async def close(self):
        await self.client.aclose()

    def tx(self) -> TestRedisStoreSession:
        return TestRedisStoreSession(self.client)
