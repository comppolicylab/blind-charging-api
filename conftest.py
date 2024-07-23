import os
import tempfile
from datetime import datetime

import pytest
import pytz
from fastapi.testclient import TestClient

os.environ["VALIDATE_TOKEN"] = "no"

TEST_CONFIG = """\
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
path = ":memory:"

[processor]
pipe = [
    { engine = "extract:tesseract" },
    { engine = "redact:noop", delimiters = ["[", "]"] },
    { engine = "render:text" },
]
"""

tmp_config = tempfile.NamedTemporaryFile()
tmp_config.write(TEST_CONFIG.encode("utf-8"))
tmp_config.flush()
os.environ["CONFIG_PATH"] = tmp_config.name


@pytest.fixture
async def config():
    from app.server.config import config

    yield config


@pytest.fixture
async def exp_db(config):
    from app.server.db import Base

    await config.experiments.store.driver.init(Base, drop_first=True)
    yield config.experiments.store.driver


@pytest.fixture
async def qstore(config):
    async with config.queue.store.driver() as store:
        yield store


@pytest.fixture
def now(request):
    default_now = datetime(2024, 1, 1, 0, 0, 0)
    dt = getattr(request, "param", default_now)
    # Make sure to use a timezone-aware datetime. By default, the timezone is UTC.
    # If we don't do this, the tests will fail when run in a different timezone.
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    return lambda: dt


@pytest.fixture
async def api(config, exp_db, now, qstore):
    from app import server

    api = TestClient(server)
    api.app.state.now = now
    api.app.state.queue_store = qstore
    api.app.state.db = exp_db

    yield api
