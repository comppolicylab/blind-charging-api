import logging
import os
from pathlib import Path
from typing import Union

import tomllib
from glowplug import MsSqlSettings, SqliteSettings
from pydantic import BaseModel
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

DbConfig = Union[MsSqlSettings, SqliteSettings]


class RetentionConfig(BaseModel):
    hours: float = 72.0


class Config(BaseSettings):
    debug: bool = False
    db: DbConfig = SqliteSettings(engine="sqlite")
    retention: RetentionConfig = RetentionConfig()
    automigrate: bool = False


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
logging.basicConfig(level=_log_level)
# Set log level for any loggers that have been instantiated before this point
_logger_names = ["root"] + list(logging.root.manager.loggerDict.keys())
_loggers = [logging.getLogger(name) for name in _logger_names]
for logger in _loggers:
    logger.setLevel(_log_level)
