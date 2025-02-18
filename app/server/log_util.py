import logging
from typing import cast

_UvicornLogRecordArgs = tuple[str, str, str, str, int]
"""Type of the args that are passed to the uvicorn.access log.

Tuple fields:
 - Host
 - Method
 - Path
 - HTTP Version
 - Status
"""


class _UvicornAccessLogFilter(logging.Filter):
    """Filter access log messages."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    @property
    def _current_level(self) -> int:
        return self._logger.level

    def filter(self, record: logging.LogRecord) -> bool:
        """Adjust log level based on message type.

        Health check logs should go to debug,
        4xx errors should go to warning,
        5xx errors should go to error.
        """
        args = cast(_UvicornLogRecordArgs, record.args)
        path, status = args[2], args[4]
        if path == "/api/v1/health" and status == 200:
            record.levelname = "DEBUG"
            record.levelno = logging.DEBUG
        if status >= 400:
            record.levelname = "WARNING"
            record.levelno = logging.WARNING
        if status >= 500:
            record.levelname = "ERROR"
            record.levelno = logging.ERROR

        # Check if we should filter this based on logger's current level.
        if record.levelno < self._current_level:
            return False

        return True


def improve_uvicorn_access_logs():
    """Configure the uvicorn access log to be more useful.

    Specifically, this adjusts the level of messages based on the content.
    When debug mode is turned off, this will have the effect of hiding the
    health check logs when the service is healthy.
    """
    uv_logger = logging.getLogger("uvicorn.access")
    uv_logger.addFilter(_UvicornAccessLogFilter(uv_logger))
