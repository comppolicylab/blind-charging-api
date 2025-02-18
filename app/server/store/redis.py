import logging
from typing import TYPE_CHECKING, Awaitable, Literal, TypeVar, cast

import redis.asyncio as aioredis
from pydantic import BaseModel
from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.asyncio.cluster import ClusterPipeline as AsyncClusterPipeline

from .store import SimpleMapping, Store, StoreSession

if TYPE_CHECKING:
    from fakeredis import FakeServer

logger = logging.getLogger(__name__)


class RedisConfig(BaseModel):
    engine: Literal["redis"] = "redis"
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ssl: bool = False
    cluster: bool = False
    password: str = ""
    user: str = ""

    @property
    def url(self) -> str:
        return self._make_url(use_cluster_scheme=False)

    @property
    def celery_url(self) -> str:
        return self._make_url(use_cluster_scheme=True)

    def _make_url(self, use_cluster_scheme: bool) -> str:
        """Make a URL for the Redis connection.

        Note that `kombu` has support for redis clusters via a special
        `rediscluster://` scheme. This is not supported by other redis
        clients, so avoid using that scheme in those cases.
        """
        pfx = "redis"
        q = ""

        if self.cluster and use_cluster_scheme:
            pfx += "cluster"

        if self.ssl:
            pfx += "s"
            q = "?ssl_cert_reqs=none"

        auth = ""
        if self.user or self.password:
            auth = f"{self.user}:{self.password}@"

        return f"{pfx}://{auth}{self.host}:{self.port}/{self.db}{q}"

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


class RedisClusterStoreSession(StoreSession):
    cluster: aioredis.RedisCluster

    pipe: AsyncClusterPipeline

    def __init__(self, cluster: aioredis.RedisCluster):
        self.cluster = cluster
        self.pipe = self.cluster.pipeline()

    async def open(self):
        await self.pipe.initialize()

    async def commit(self):
        logger.debug(
            "Committing the cluster pipeline with %d commands",
            len(self.pipe._command_stack),
        )
        results = await self.pipe.execute(allow_redirections=True, raise_on_error=True)
        logger.debug("Redis cluster pipeline commit results: %r", results)

    def _key_func(self, key):
        """Add the RBC hash tag to the key.

        See https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/#hash-tags
        for more information. The Hash Tag allows us to ensure that all keys
        are stored on the same slot in the cluster.
        """
        return f"{{rbc}}:{key}"

    async def rollback(self):
        logger.debug("Requesting a reset of the cluster pipeline")
        await self.pipe.reset()

    async def ping(self):
        return await self.cluster.ping()

    async def time(self) -> int:
        t, _ = await self.cluster.time()  # type: ignore[attr-defined]
        return t

    async def close(self):
        return

    async def get(self, key: str) -> bytes | None:
        k = self._key_func(key)
        result = await self.cluster.get(k)  # type: ignore[attr-defined]
        return result

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        k = self._key_func(key)
        return await _maybe_wait(self.cluster.hgetall(k))  # type: ignore[attr-defined]

    async def enqueue(self, key: str, value: str):
        k = self._key_func(key)
        self.pipe.lpush(k, value)  # type: ignore[attr-defined]

    async def dequeue(self, key: str) -> bytes | None:
        k = self._key_func(key)
        p = cast(Awaitable[bytes | None] | bytes | None, self.cluster.rpop(k))  # type: ignore[attr-defined]
        return await _maybe_wait(p)

    async def set(self, key: str, value: str | bytes):
        k = self._key_func(key)
        self.pipe.set(k, value)  # type: ignore[attr-defined]

    async def sadd(self, key: str, *value):
        k = self._key_func(key)
        self.pipe.sadd(k, *value)  # type: ignore[attr-defined]

    async def expire_at(self, key: str, expire_at: int):
        k = self._key_func(key)
        logger.debug("Redis cluster expireat %r -> %r", k, expire_at)
        self.pipe.expireat(k, expire_at)  # type: ignore[attr-defined]

    async def hsetmapping(self, key: str, mapping: SimpleMapping):
        k = self._key_func(key)
        self.pipe.hset(k, mapping=dict(mapping))  # type: ignore[attr-defined]


class BaseSimpleRedisStoreSession(StoreSession):
    client: aioredis.Redis
    pipe: AsyncPipeline

    async def open(self):
        pass

    async def commit(self):
        return await self.pipe.execute()

    async def rollback(self):
        await self.pipe.discard()

    async def ping(self):
        return await self.client.ping()

    async def time(self) -> int:
        t, _ = await self.client.time()
        return t

    async def close(self):
        await self.client.aclose(close_connection_pool=False)

    async def set(self, key: str, value: str | bytes):
        await self.pipe.set(key, value)

    async def get(self, key: str) -> bytes | None:
        # TODO(jnu): We can't watch a key in the middle of a pipeline.
        # Will have to come up with a way to pre-register keys to watch.
        # Can probably do this easily just by watching all the keys we
        # ever use, even though in some cases we won't use all of them.
        # await self.pipe.watch(key)
        return await self.client.get(key)

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return await _maybe_wait(self.client.hgetall(key))

    async def dequeue(self, key: str) -> bytes | None:
        # NOTE: using a cast here because the typing is misleading in the library.
        # The library gives `str | list | None`. This should really be written using
        # overloads. The `list` is only returned when `count` is passed, which we don't
        # do here. Any `str` is only returned when `decode_responses` is set during the
        # initialization of the client. In our app, at least for now, we will return
        # bytes. So, the correct type here is really `bytes | None`.
        p = cast(Awaitable[bytes | None] | bytes | None, self.client.rpop(key))
        return await _maybe_wait(p)

    async def hsetmapping(self, key: str, mapping: SimpleMapping):
        # NOTE: using a `dict` call here since our typings are a little
        # more broad than the redis library technically accepts. This should
        # really be a no-op in most cases.
        await _maybe_wait(self.pipe.hset(key, mapping=dict(mapping)))

    async def expire_at(self, key: str, expire_at: int):
        await self.pipe.expireat(key, expire_at)

    async def enqueue(self, key: str, value: str):
        await _maybe_wait(self.pipe.lpush(key, value))

    async def sadd(self, key: str, *value):
        await _maybe_wait(self.pipe.sadd(key, *value))


class RedisStoreSession(BaseSimpleRedisStoreSession):
    client: aioredis.Redis

    def __init__(self, pool: aioredis.ConnectionPool):
        self.pool = pool
        self.client: aioredis.Redis = aioredis.Redis(connection_pool=self.pool)
        self.pipe = self.client.pipeline(transaction=True)
        self.pipe.multi()


class TestRedisStoreSession(BaseSimpleRedisStoreSession):
    client: aioredis.Redis

    def __init__(self, client):
        self.client = client
        self.pipe = self.client.pipeline(transaction=True)


class RedisStore(Store):
    def __init__(self, config: RedisConfig):
        self.config = config
        self.pool: aioredis.ConnectionPool | None = None
        self.cluster: aioredis.RedisCluster | None = None

    async def init(self):
        if self.config.cluster:
            self.cluster = aioredis.RedisCluster.from_url(self.config.url)
        else:
            self.pool = aioredis.ConnectionPool.from_url(self.config.url)

    async def close(self):
        if self.pool:
            await self.pool.aclose()
        if self.cluster:
            await self.cluster.aclose()

    def tx(
        self,
    ) -> RedisStoreSession | RedisClusterStoreSession | TestRedisStoreSession:
        if self.pool:
            return RedisStoreSession(self.pool)
        elif self.cluster:
            return RedisClusterStoreSession(self.cluster)
        else:
            raise ValueError("Store is not initialized")


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
