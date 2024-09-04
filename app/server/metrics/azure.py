from functools import cached_property
from typing import Literal

from azure.monitor.opentelemetry.exporter import (
    AzureMonitorLogExporter,
    AzureMonitorMetricExporter,
    AzureMonitorTraceExporter,
)
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from .base import BaseMetricsDriver


class AzureMonitorMetricsConfig(BaseModel):
    engine: Literal["azure"] = "azure"
    connection_string: str

    @cached_property
    def driver(self):
        return AzureMonitorMetricsDriver(connection_string=self.connection_string)


class AzureMonitorMetricsDriver(BaseMetricsDriver):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._metrics: AzureMonitorMetricExporter | None = None
        self._trace: AzureMonitorTraceExporter | None = None
        self._log: AzureMonitorLogExporter | None = None

    async def __aenter__(self):
        self._metrics = AzureMonitorMetricExporter(
            connection_string=self.connection_string
        )
        self._trace = AzureMonitorTraceExporter(
            connection_string=self.connection_string
        )
        self._log = AzureMonitorLogExporter(connection_string=self.connection_string)

        self._instrument()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._metrics.shutdown()
        self._trace.shutdown()
        self._log.shutdown()
        self._metrics = None
        self._trace = None
        self._log = None

    def _instrument(self):
        FastAPIInstrumentor().instrument()
        CeleryInstrumentor().instrument()
