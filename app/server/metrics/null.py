from functools import cached_property
from typing import Literal

from pydantic import BaseModel

from .base import BaseMetricsDriver


class NoMetricsConfig(BaseModel):
    engine: Literal["none"] = "none"

    @cached_property
    def driver(self):
        return NoMetricsDriver()


class NoMetricsDriver(BaseMetricsDriver):
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
