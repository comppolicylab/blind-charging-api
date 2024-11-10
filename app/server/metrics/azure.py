import logging
from functools import cached_property
from typing import Literal

from azure.monitor.opentelemetry import configure_azure_monitor
from pydantic import BaseModel

from .base import BaseMetricsDriver

logger = logging.getLogger(__name__)


class AzureMonitorMetricsConfig(BaseModel):
    engine: Literal["azure"] = "azure"
    connection_string: str

    @cached_property
    def driver(self):
        return AzureMonitorMetricsDriver(connection_string=self.connection_string)


class AzureMonitorMetricsDriver(BaseMetricsDriver):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    async def __aenter__(self):
        self._init()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._cleanup()

    def _init(self):
        configure_azure_monitor(connection_string=self.connection_string)

        logger.info("Azure Monitor metrics driver initialized.")

        return self

    def _cleanup(self):
        pass
