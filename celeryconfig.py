override_backends = {
    "rediscluster": "app.lib.backend.rediscluster:RedisClusterBackend",
    "redisclusters": "app.lib.backend.rediscluster:RedisClusterBackend",
}

print("LOADED CELERY CONFIG")
