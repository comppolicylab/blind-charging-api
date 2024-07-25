import logging
import os
import tempfile
from datetime import datetime

import pytest
import pytz
from fastapi.testclient import TestClient

DEFAULT_TEST_CONFIG_TPL = """\
debug = true

[queue]
[queue.store]
engine = "test-redis"

[queue.broker]
engine = "test-redis"

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
def logger():
    return logging.getLogger(__name__)


@pytest.fixture
async def sqlite_db_path(request, logger):
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
async def config(sqlite_db_path, request, logger):
    from app.server.config import config

    os.environ["VALIDATE_TOKEN"] = "no"

    cfg_tpl = getattr(request, "param", DEFAULT_TEST_CONFIG_TPL)
    cfg = cfg_tpl.format(db_path=sqlite_db_path)

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
async def exp_db(config):
    from app.server.db import init_db

    await init_db(config.experiments.store.driver, drop_first=True)

    yield config.experiments.store.driver


@pytest.fixture
async def qstore(config):
    async with config.queue.store.driver() as store:
        yield store


@pytest.fixture
def now(request, logger):
    default_now = datetime(2024, 1, 1, 0, 0, 0)
    dt = getattr(request, "param", default_now)
    # Make sure to use a timezone-aware datetime. By default, the timezone is UTC.
    # If we don't do this, the tests will fail when run in a different timezone.
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    logger.debug("What time is it? now = %s", dt)
    return lambda: dt


@pytest.fixture
async def api(config, exp_db, now, qstore):
    from app import server

    api = TestClient(server)
    api.app.state.now = now
    api.app.state.queue_store = qstore
    api.app.state.db = exp_db

    yield api
