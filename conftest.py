import logging
import os
import tempfile
from datetime import datetime
from typing import TYPE_CHECKING, AsyncGenerator, Callable, Generator
from urllib.parse import urlparse

import pytest
import pytz
from fastapi.testclient import TestClient
from glowplug import DbDriver
from pytest_celery import (
    CeleryBackendCluster,
    CeleryBrokerCluster,
    CeleryTestSetup,
    RedisTestBackend,
)

if TYPE_CHECKING:
    from app.server.config import LazyObjectProxy


DEFAULT_TEST_CONFIG_TPL = """\
debug = true

[queue]
[queue.store]
{queue_store_config}

[queue.broker]
{queue_broker_config}

[experiments]
enabled = true
automigrate = true

[experiments.store]
engine = "sqlite"
path = "{db_path}"

[processor]
pipe = [
    {{ engine = "extract:tesseract" }},
    {{ engine = "redact:noop", delimiters = ["[", "]"] }},
    {{ engine = "render:text" }},
]
"""

os.environ["VALIDATE_TOKEN"] = "no"


@pytest.fixture
def logger() -> logging.Logger:
    """Logging for tests."""
    return logging.getLogger(__name__)


@pytest.fixture
def sqlite_db_path(request, logger) -> Generator[str, None, None]:
    """Fixture to provide a path to a SQLite database.

    Path will be materialized as a temporary file on disk for the test.
    """
    db_path = getattr(request, "param", None)
    if db_path:
        logger.debug("Using existing SQLite database: %s", db_path)
        with open(db_path, "w") as f:
            f.write("")
        yield db_path
    else:
        logger.debug("Creating temporary SQLite database ...")
        with tempfile.NamedTemporaryFile() as tmp_db:
            logger.debug("Using temporary SQLite database: %s", tmp_db.name)
            yield tmp_db.name


@pytest.fixture
def celery_backend_cluster(
    celery_redis_backend: RedisTestBackend,
) -> Generator[CeleryBackendCluster, None, None]:
    """Fixture for the Celery backend cluster.

    Currently we only run tests with the Redis backend, as it
    is the only one we have committed to supporting in production.
    """
    cluster = CeleryBackendCluster(celery_redis_backend)
    yield cluster
    cluster.teardown()


@pytest.fixture
def celery_broker_cluster(
    celery_redis_broker: RedisTestBackend,
) -> Generator[CeleryBrokerCluster, None, None]:
    """Fixture for the Celery broker cluster.

    Currently we only run tests with the Redis broker, as it
    is the only one we have committed to supporting in production.
    """
    cluster = CeleryBrokerCluster(celery_redis_broker)
    yield cluster
    cluster.teardown()


@pytest.fixture
def config(sqlite_db_path, request, logger) -> Generator["LazyObjectProxy", None, None]:
    """Generate a configuration file for a single test.

    The config file is parameterized with the paths to the SQLite database
    and the Redis instances used for the Celery backend and broker.
    """
    from app.server.config import config

    os.environ["VALIDATE_TOKEN"] = "no"

    # Allow override of the config template with a parameter.
    cfg_tpl = getattr(request, "param", DEFAULT_TEST_CONFIG_TPL)

    # Render the config settings for the queue store and broker.
    if "real_queue" in request.fixturenames:
        # When the "real queue" was requested, we will stand up a real celery
        # cluster and point this config to use it.
        celery_setup: CeleryTestSetup = request.getfixturevalue("celery_setup")

        backend_config = celery_setup.backend.config()
        backend_redis_host = urlparse(backend_config["host_url"]).hostname
        queue_store_config = "\n".join(
            [
                'engine = "redis"',
                f'host = "{backend_redis_host}"',
                f"port = {backend_config['port']}",
                f"database = {backend_config['vhost']}",
            ]
        )

        broker_config = celery_setup.broker.config()
        broker_redis_host = urlparse(broker_config["host_url"]).hostname
        queue_broker_config = "\n".join(
            [
                'engine = "redis"',
                f'host = "{broker_redis_host}"',
                f"port = {broker_config['port']}",
                f"database = {broker_config['vhost']}",
            ]
        )
    else:
        # Fake redis is *much* faster than real redis, and zero config.
        queue_store_config = 'engine = "test-redis"'
        queue_broker_config = 'engine = "test-redis"'

    # Create a new config file with the test parameters
    cfg = cfg_tpl.format(
        db_path=sqlite_db_path,
        queue_store_config=queue_store_config,
        queue_broker_config=queue_broker_config,
    )

    with tempfile.NamedTemporaryFile() as tmp_config:
        # Write new config to a temporary file
        tmp_config.write(cfg.encode("utf-8"))
        tmp_config.flush()

        # Reset the config in memory to point to new file.
        config._reset(tmp_config.name)
        logger.debug(f"Using config: {tmp_config.name}")
        logger.debug(f"Full Config:\n===\n\n{cfg}\n\n===")

        yield config


@pytest.fixture
async def exp_db(config) -> AsyncGenerator[DbDriver, None]:
    """Initialize the database for experiments."""
    from app.server.db import init_db

    await init_db(config.experiments.store.driver, drop_first=True)

    yield config.experiments.store.driver


@pytest.fixture
def now(request, logger) -> Callable[[], datetime]:
    """Fixture to provide a timezone-aware datetime object.

    This is the moment in time that the test will think it is.
    """
    default_now = datetime(2024, 1, 1, 0, 0, 0)
    dt = getattr(request, "param", default_now)
    # Make sure to use a timezone-aware datetime. By default, the timezone is UTC.
    # If we don't do this, the tests will fail when run in a different timezone.
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    logger.debug("What time is it? now = %s", dt)
    return lambda: dt


@pytest.fixture
def api(config, exp_db, now) -> Generator[TestClient, None, None]:
    """Fixture to provide a test client for the FastAPI app.

    The fixture will reference the database and message queue objects.
    """
    from app import server

    with TestClient(server) as api:
        # NOTE: need to make sure the queue store is initialized within
        # the context of the TestClient's event loop. This is _not_ the
        # same event loop that is managed by the pytest-asyncio `event_loop`
        # fixture. There will be a race condition in the teardown if we
        # manage the queue store outside of this context.
        store_driver = api.portal.wrap_async_context_manager(
            config.queue.store.driver()
        )
        with store_driver as qstore:
            api.app.state.now = now
            api.app.state.queue_store = qstore
            api.app.state.db = exp_db

            yield api


@pytest.fixture
def real_queue(celery_setup):
    """Fixture to provide a real Celery queue for testing."""
    return celery_setup
