from typing import TYPE_CHECKING, Awaitable, Literal, TypeVar, cast

import redis.asyncio as aioredis
from pydantic import BaseModel
from redis.asyncio.client import Pipeline as AsyncPipeline

from .store import SimpleMapping, Store, StoreSession

if TYPE_CHECKING:
    from fakeredis import FakeServer


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

    _server: "FakeServer | None" = None

    def driver(self) -> "TestRedisStore":
        return TestRedisStore(self)

    @property
    def server(self):
        from fakeredis import FakeServer

        if self._server is None:
            self._server = FakeServer()
        return self._server

    def reset(self):
        del self._server
        self._server = None


T = TypeVar("T")


async def _maybe_wait(val: Awaitable[T] | T) -> T:
    if isinstance(val, Awaitable):
        return await val
    return val


class BaseRedisStoreSession(StoreSession):
    pipe: AsyncPipeline
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
        # TODO(jnu): We can't watch a key in the middle of a pipeline.
        # Will have to come up with a way to pre-register keys to watch.
        # Can probably do this easily just by watching all the keys we
        # ever use, even though in some cases we won't use all of them.
        # await self.pipe.watch(key)
        return await self.client.get(key)

    async def sadd(self, key: str, *value):
        await _maybe_wait(self.pipe.sadd(key, *value))

    async def hsetmapping(self, key: str, mapping: SimpleMapping):
        # NOTE: using a `dict` call here since our typings are a little
        # more broad than the redis library technically accepts. This should
        # really be a no-op in most cases.
        await _maybe_wait(self.pipe.hset(key, mapping=dict(mapping)))

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return await _maybe_wait(self.client.hgetall(key))

    async def expire_at(self, key: str, expire_at: int):
        await self.pipe.expireat(key, expire_at)

    async def enqueue(self, key: str, value: str):
        await _maybe_wait(self.pipe.lpush(key, value))

    async def dequeue(self, key: str) -> bytes | None:
        # NOTE: using a cast here because the typing is misleading in the library.
        # The library gives `str | list | None`. This should really be written using
        # overloads. The `list` is only returned when `count` is passed, which we don't
        # do here. Any `str` is only returned when `decode_responses` is set during the
        # initialization of the client. In our app, at least for now, we will return
        # bytes. So, the correct type here is really `bytes | None`.
        p = cast(Awaitable[bytes | None] | bytes | None, self.client.rpop(key))
        return await _maybe_wait(p)

    async def time(self) -> int:
        t, _ = await self.client.time()
        return t


class RedisStoreSession(BaseRedisStoreSession):
    def __init__(self, pool: aioredis.ConnectionPool):
        self.pool = pool
        self.client = aioredis.Redis(connection_pool=self.pool)
        self.pipe = self.client.pipeline(transaction=True)
        self.pipe.multi()


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

        self.client = FakeAsyncRedis(server=self.config.server)

    async def close(self):
        await self.client.aclose()

    def tx(self) -> TestRedisStoreSession:
        return TestRedisStoreSession(self.client)
