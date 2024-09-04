from abc import ABC, abstractmethod
from typing import TypeVar

T = TypeVar("T", bound="BaseMetricsDriver")


class BaseMetricsDriver(ABC):
    @abstractmethod
    async def __aenter__(self: T) -> T: ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb): ...
