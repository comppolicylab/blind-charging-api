import logging
import os
from pathlib import Path
from typing import Annotated, Union

import tomllib
from bc2.core.pipeline import (
    ExtractConfig,
    ParseConfig,
    RedactConfig,
    RenderConfig,
)
from glowplug import SqliteSettings
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from .authn import AuthnConfig, NoAuthnConfig
from .db import RdbmsConfig
from .lazy import LazyObjectProxy
from .log_util import improve_uvicorn_access_logs
from .metrics import AzureMonitorMetricsConfig, NoMetricsConfig
from .store import RedisConfig, RedisTestConfig

logger = logging.getLogger(__name__)


FOUR_HOURS_S = 4 * 60 * 60
"""Four hours in seconds."""


class TaskConfig(BaseModel):
    retention_time_seconds: int = FOUR_HOURS_S
    max_retries: int = 3
    retry_interval: float = 60.0
    callback_timeout_seconds: float = 30.0
    link_download_timeout_seconds: float = 30.0


StoreConfig = Union[RedisConfig, RedisTestConfig]

BrokerConfig = Union[RedisConfig, RedisTestConfig]

MetricsConfig = Union[AzureMonitorMetricsConfig, NoMetricsConfig]


AnyPipelineProcessingConfig = Union[
    ExtractConfig, ParseConfig, RedactConfig, RenderConfig
]


class InlineProcessorConfig(BaseModel):
    """Define the pipeline directly in the main file."""

    pipe: list[AnyPipelineProcessingConfig]


class ExternalProcessorConfig(BaseModel):
    """Load the pipeline from a file or an environment variable."""

    @property
    def pipe(self) -> list[AnyPipelineProcessingConfig]:
        pipe_string = self._load_pipe_string()
        try:
            data = tomllib.loads(pipe_string)
            return data["pipe"]
        except Exception as e:
            raise ValueError("Invalid pipeline configuration") from e

    def _load_pipe_string(self) -> str:
        raise NotImplementedError("Subclasses must implement this method.")


class FileProcessorConfig(ExternalProcessorConfig):
    """Load the pipeline from a file."""

    pipe_file: str

    def _load_pipe_string(self) -> str:
        """Load the raw pipeline text from a file."""
        return Path(self.pipe_file).read_text()


class EnvProcessorConfig(ExternalProcessorConfig):
    """Load the pipeline from an environment variable."""

    pipe_env: str

    def _load_pipe_string(self) -> str:
        """Load the raw pipeline text from an environment variable."""
        s = os.getenv(self.pipe_env)
        if s is None:
            raise ValueError(f"Environment variable {self.pipe_env} is not set")
        return s


ProcessorConfig = Union[InlineProcessorConfig, FileProcessorConfig, EnvProcessorConfig]


class QueueConfig(BaseModel):
    task: TaskConfig = TaskConfig()
    store: StoreConfig = RedisConfig()
    broker: BrokerConfig = RedisConfig()
    concurrency: int = 10


class ExperimentsConfig(BaseModel):
    enabled: bool = False
    automigrate: Annotated[bool, Field(deprecated=True)] = True
    store: RdbmsConfig = SqliteSettings(engine="sqlite")
    config_reload_interval: float = 30.0


class Config(BaseSettings):
    debug: bool = False
    queue: QueueConfig = QueueConfig()
    experiments: ExperimentsConfig = ExperimentsConfig()
    metrics: MetricsConfig = NoMetricsConfig()
    authentication: AuthnConfig = NoAuthnConfig()
    processor: ProcessorConfig


def _load_config(path: str = os.getenv("CONFIG_PATH", "config.toml")) -> Config:
    """Load the configuration from a TOML file."""
    if not Path(path).exists():
        logger.warning(f"Config file not found: {path}")
        return Config(processor=InlineProcessorConfig(pipe=[]))
    else:
        logger.info(f"Loading config file: {path}")
    raw_cfg = Path(path).read_text()
    cfg = tomllib.loads(raw_cfg)
    return Config.model_validate(cfg)


config = LazyObjectProxy(_load_config)
"""The application configuration.

This is a shared global object. It's instantiated lazily,
when the first attribute is accessed.

The config can be reloaded by calling `config._reset()`.

By default the configuration is loaded from the `CONFIG_PATH`
environment variable, or from `config.toml` in the current directory.
"""


# Set up logging
# NOTE(jnu): Need to import all libraries that set up logging before this point.
# Alligater logs are useful to see, so make sure that's set up.
_log_level = logging.DEBUG if config.debug else logging.INFO
logging.basicConfig(level=logging.WARNING)
# Set log level for any loggers that have been instantiated before this point.
# Most loggers should be set to WARNING, but some should be set to INFO or DEBUG.
_logger_names = ["root"] + list(logging.root.manager.loggerDict.keys())
_loud_logger_names = [
    "uvicorn",
    "fastapi",
    "app.",
    "cli.",
    "pydantic",
    "celery",
    "alligater",
    "bc2",
]
for name in _logger_names:
    for loud_name in _loud_logger_names:
        if name.startswith(loud_name):
            logging.getLogger(name).setLevel(_log_level)
            break
    else:
        logging.getLogger(name).setLevel(logging.WARNING)

# Apply some custom filtering to uvicorn's logs.
improve_uvicorn_access_logs()
