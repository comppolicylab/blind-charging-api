import logging
import os
from pathlib import Path
from typing import Union

import tomllib
from blind_charging_core.pipeline import (
    ExtractConfig,
    ParseConfig,
    RedactConfig,
    RenderConfig,
)
from glowplug import MsSqlSettings, SqliteSettings
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from .store import RedisConfig, RedisTestConfig

logger = logging.getLogger(__name__)


RdbmsConfig = Union[MsSqlSettings, SqliteSettings]


class TaskConfig(BaseModel):
    retention_hours: float = 72.0
    max_retries: int = 3
    retry_interval: float = 60.0
    callback_timeout_seconds: float = 30.0
    link_download_timeout_seconds: float = 30.0


StoreConfig = Union[RedisConfig, RedisTestConfig]

BrokerConfig = Union[RedisConfig, RedisTestConfig]


AnyPipelineProcessingConfig = Union[
    ExtractConfig, ParseConfig, RedactConfig, RenderConfig
]


class ProcessorConfig(BaseModel):
    pipe: list[AnyPipelineProcessingConfig]


class QueueConfig(BaseModel):
    task: TaskConfig = TaskConfig()
    store: StoreConfig = RedisConfig()
    broker: BrokerConfig = RedisConfig()


class ExperimentsConfig(BaseModel):
    enabled: bool = False
    automigrate: bool = False
    store: RdbmsConfig = SqliteSettings(engine="sqlite")


class Config(BaseSettings):
    debug: bool = False
    queue: QueueConfig = QueueConfig()
    experiments: ExperimentsConfig = ExperimentsConfig()
    processor: ProcessorConfig


def _load_config(path: str = os.getenv("CONFIG_PATH", "config.toml")) -> Config:
    """Load the configuration from a TOML file."""
    if not Path(path).exists():
        logger.warning(f"Config file not found: {path}")
        return Config()
    raw_cfg = Path(path).read_text()
    cfg = tomllib.loads(raw_cfg)
    return Config.model_validate(cfg)


class LazyObjectProxy:
    """A proxy object that lazily loads an object when an attribute is accessed."""

    def __init__(self, loader, *args, **kwargs):
        """Create a new LazyObjectProxy.

        Args:
            loader (callable): A function that returns the object to be proxied.
            *args: Positional arguments to pass to the loader.
            **kwargs: Keyword arguments to pass to the loader.
        """
        self._loader = (loader, args, kwargs)
        self._obj = None

    def __getattr__(self, name):
        if self._obj is None:
            f, args, kwargs = self._loader
            self._obj = f(*args, **kwargs)
        return getattr(self._obj, name)

    def _reset(self, *args, **kwargs):
        """Delete the cached object and reset the loader.

        The next time an attribute is accessed, the loader will be
        called with the new arguments.

        Args:
            *args: Positional arguments to pass to the loader.
            **kwargs: Keyword arguments to pass to the loader.
        """
        del self._obj
        self._obj = None
        self._loader = (self._loader[0], args, kwargs)


config = LazyObjectProxy(_load_config)
"""The application configuration.

This is a shared global object. It's instantiated lazily,
when the first attribute is accessed.

The config can be reloaded by calling `config._reset()`.

By default the configuration is loaded from the `CONFIG_PATH`
environment variable, or from `config.toml` in the current directory.
"""


# Set up logging
_log_level = logging.DEBUG if config.debug else logging.INFO
logging.basicConfig(level=logging.WARNING)
# Set log level for any loggers that have been instantiated before this point.
# Most loggers should be set to WARNING, but some should be set to INFO or DEBUG.
_logger_names = ["root"] + list(logging.root.manager.loggerDict.keys())
_loud_logger_names = [
    "uvicorn",
    "fastapi",
    "app.",
    "pydantic",
    "celery",
    "alligater",
    "blind_charging_core",
]
for name in _logger_names:
    for loud_name in _loud_logger_names:
        if name.startswith(loud_name):
            logging.getLogger(name).setLevel(_log_level)
            break
    else:
        logging.getLogger(name).setLevel(logging.WARNING)
