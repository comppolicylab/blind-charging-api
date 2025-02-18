# -*- coding: utf-8 -*-
"""Support for RedisCluster as a Celery backend.

This is adapted from the work done here:
https://github.com/hbasria/celery-redis-cluster-backend

Most of the code is the work of the original authors.
The interface has been updated and adapted to work better with the current
implementation of RedisCluster in the redis-py library, and newer versions
of Celery, Kombu, and Python.
"""

from functools import partial
from urllib.parse import unquote

import redis
from celery import states
from celery.backends.base import KeyValueStoreBackend
from celery.canvas import maybe_signature
from celery.exceptions import ChordError, ImproperlyConfigured
from celery.utils.log import get_logger
from celery.utils.serialization import strtobool
from celery.utils.time import humanize_seconds
from kombu.utils import cached_property, retry_over_time
from kombu.utils.url import _parse_url

RedisCluster = getattr(redis, "RedisCluster", None)


__all__ = ["RedisClusterBackend"]

REDIS_MISSING = """\
You need to install the redis-py library in order to use \
the Redis result store backend."""

REDIS_NON_0_DB = """\
Redis only supports the default DB when using the Redis result store."""

REDIS_URL_MISSING = """\
You need to specify a URL for the Redis result store backend."""

logger = get_logger(__name__)
error = logger.error


class RedisClusterBackend(KeyValueStoreBackend):
    """Celery RedisCluster backend."""

    redis = RedisCluster

    startup_nodes = None
    max_connections = None
    init_slot_cache = True

    supports_autoexpire = True
    supports_native_join = True
    implements_incr = True

    def __init__(self, url: str | None = None, **kwargs):
        super(RedisClusterBackend, self).__init__(expires_type=int, **kwargs)

        if self.redis is None:
            raise ImproperlyConfigured(REDIS_MISSING)

        if not url:
            raise ImproperlyConfigured(REDIS_URL_MISSING)

        self.url = url
        self.conn_params = self._parse_params_from_url(url)

        try:
            new_join = strtobool(self.conn_params.pop("new_join"))
            if new_join:
                self.apply_chord = self._new_chord_apply
                self.on_chord_part_return = self._new_chord_return

        except KeyError:
            pass

        self.expires = self.prepare_expires(None, type=int)
        self.connection_errors = ()
        self.channel_errors = ()

    def _parse_params_from_url(self, url: str) -> dict:
        if not url:
            return {}

        ssl_param_keys = [
            "ssl_ca_certs",
            "ssl_certfile",
            "ssl_keyfile",
            "ssl_cert_reqs",
        ]

        scheme, host, port, _, password, path, query = _parse_url(url)
        uses_ssl = scheme == "redisclusters"

        # Get DB from path. For the RedisCluster, only 0 is supported.
        # Throw an error to reduce potential confusion if any other DB
        # is specified.
        if path and path.startswith("/"):
            db = path.lstrip("/")
            if db and db != "0":
                raise ImproperlyConfigured(REDIS_NON_0_DB)

        conn_params = {
            "host": host,
            "port": port,
            "password": password,
            "ssl": uses_ssl,
        }

        other_param_keys = ["max_connections", "new_join"]
        for key in other_param_keys:
            val = query.pop(key, None)
            if val is not None:
                conn_params[key] = unquote(val)

        if uses_ssl:
            for ssl_setting in ssl_param_keys:
                ssl_val = query.pop(ssl_setting, None)
                if ssl_val:
                    conn_params[ssl_setting] = unquote(ssl_val)

        return conn_params

    def get(self, key):
        return self.client.get(key)

    def mget(self, keys):
        return self.client.mget(keys)

    def ensure(self, fun, args, **policy):
        retry_policy = dict(self.retry_policy, **policy)
        max_retries = retry_policy.get("max_retries")
        return retry_over_time(
            fun,
            self.connection_errors,
            args,
            {},
            partial(self.on_connection_error, max_retries),
            **retry_policy,
        )

    def on_connection_error(self, max_retries, exc, intervals, retries):
        tts = next(intervals)
        error(
            "Connection to Redis lost: Retry (%s/%s) %s.",
            retries,
            max_retries or "Inf",
            humanize_seconds(tts, "in "),
        )
        return tts

    def set(self, key, value, **retry_policy):
        return self.ensure(self._set, (key, value), **retry_policy)

    def _set(self, key, value):
        self.client.set(key, value)

        if hasattr(self, "expires"):
            self.client.expire(key, self.expires)

    def delete(self, key):
        self.client.delete(key)

    def incr(self, key):
        return self.client.incr(key)

    def expire(self, key, value):
        return self.client.expire(key, value)

    def add_to_chord(self, group_id, result):
        self.client.incr(self.get_key_for_group(group_id, ".t"), 1)

    def _unpack_chord_result(
        self,
        tup,
        decode,
        EXCEPTION_STATES=states.EXCEPTION_STATES,
        PROPAGATE_STATES=states.PROPAGATE_STATES,
    ):
        _, tid, state, retval = decode(tup)
        if state in EXCEPTION_STATES:
            retval = self.exception_to_python(retval)
        if state in PROPAGATE_STATES:
            raise ChordError("Dependency {0} raised {1!r}".format(tid, retval))
        return retval

    def _new_chord_apply(
        self,
        header,
        partial_args,
        group_id,
        body,
        result=None,
        options: dict | None = None,
        **kwargs,
    ):
        options = options or {}
        # avoids saving the group in the redis db.
        options["task_id"] = group_id
        return header(*partial_args, **options or {})

    def _new_chord_return(
        self,
        task,
        state,
        result,
        propagate=None,
        PROPAGATE_STATES=states.PROPAGATE_STATES,
    ):
        app = self.app
        if propagate is None:
            propagate = self.app.conf.CELERY_CHORD_PROPAGATES
        request = task.request
        tid, gid = request.id, request.group
        if not gid or not tid:
            return

        client = self.client
        jkey = self.get_key_for_group(gid, ".j")
        tkey = self.get_key_for_group(gid, ".t")
        result = self.encode_result(result, state)
        _, readycount, totaldiff, _, _ = (
            client.pipeline()
            .rpush(jkey, self.encode([1, tid, state, result]))
            .llen(jkey)
            .get(tkey)
            .expire(jkey, 86400)
            .expire(tkey, 86400)
            .execute()
        )

        totaldiff = int(totaldiff or 0)

        try:
            callback = maybe_signature(request.chord, app=app)
            total = callback["chord_size"] + totaldiff
            if readycount == total:
                decode, unpack = self.decode, self._unpack_chord_result
                resl, _, _ = (
                    client.pipeline()
                    .lrange(jkey, 0, total)
                    .delete(jkey)
                    .delete(tkey)
                    .execute()
                )
                try:
                    callback.delay([unpack(tup, decode) for tup in resl])
                except Exception as exc:
                    error(
                        "Chord callback for %r raised: %r",
                        request.group,
                        exc,
                        exc_info=1,
                    )
                    app._tasks[callback.task].backend.fail_from_current_stack(
                        callback.id,
                        exc=ChordError("Callback error: {0!r}".format(exc)),
                    )
        except ChordError as exc:
            error("Chord %r raised: %r", request.group, exc, exc_info=1)
            app._tasks[callback.task].backend.fail_from_current_stack(
                callback.id,
                exc=exc,
            )
        except Exception as exc:
            error("Chord %r raised: %r", request.group, exc, exc_info=1)
            app._tasks[callback.task].backend.fail_from_current_stack(
                callback.id,
                exc=ChordError("Join error: {0!r}".format(exc)),
            )

    @cached_property
    def client(self):
        return RedisCluster(**self.conn_params)

    def __reduce__(self, args: tuple = (), kwargs: dict | None = None):
        kwargs = kwargs or {}
        return super().__reduce__(
            args, dict(kwargs, expires=self.expires, url=self.url)
        )
