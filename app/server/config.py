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
from glowplug import SqliteSettings
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from .authn import AuthnConfig, NoAuthnConfig
from .db import RdbmsConfig
from .lazy import LazyObjectProxy
from .metrics import AzureMonitorMetricsConfig, NoMetricsConfig
from .store import RedisConfig, RedisTestConfig

logger = logging.getLogger(__name__)


class TaskConfig(BaseModel):
    retention_hours: float = 72.0
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


class ExperimentsConfig(BaseModel):
    enabled: bool = False
    automigrate: bool = False
    store: RdbmsConfig = SqliteSettings(engine="sqlite")


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
        return Config()
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
