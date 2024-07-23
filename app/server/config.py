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


# The global app configuration
config = _load_config()

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
