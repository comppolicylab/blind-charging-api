import json
from abc import ABC, abstractmethod
from typing import Mapping, Union

from pydantic import BaseModel

SimpleType = Union[bytes, memoryview, str, int, float]

SimpleMapping = Mapping[str | bytes, bytes | float | int | str]


class StoreSession(ABC):
    @abstractmethod
    async def open(self): ...

    @abstractmethod
    async def commit(self): ...

    @abstractmethod
    async def rollback(self): ...

    @abstractmethod
    async def close(self): ...

    @abstractmethod
    async def ping(self): ...

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # Commit the transaction if no exceptions were raised
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()
        await self.close()

    @abstractmethod
    async def set(self, key: str, value: str): ...

    @abstractmethod
    async def get(self, key: str) -> bytes | None: ...

    @abstractmethod
    async def sadd(self, key: str, *value: SimpleType): ...

    async def sadddict(self, key: str, value: dict[str, SimpleType]):
        """Add a dictionary of values to a set.

        This is not normally supported by Redis; we just add ser/de
        on top of native sadd.

        Args:
            key (str): The key of the set.
            value (dict[str, SimpleType]): The value to add.
        """
        await self.sadd(key, json.dumps(value, sort_keys=True))

    async def saddmodel(self, key: str, value: BaseModel):
        """Add a Pydantic model to a set.

        Args:
            key (str): The key of the set.
            value (BaseModel): The value to add.
        """
        await self.sadddict(key, value.model_dump())

    async def setdict(self, key: str, value: dict[str, SimpleType]):
        """Set a dictionary of values.

        Args:
            key (str): The key of the set.
            value (dict[str, SimpleType]): The value to set.
        """
        await self.set(key, json.dumps(value, sort_keys=True))

    async def setmodel(self, key: str, value: BaseModel):
        """Set a Pydantic model.

        Args:
            key (str): The key of the set.
            value (BaseModel): The value to set.
        """
        await self.setdict(key, value.model_dump())

    @abstractmethod
    async def hsetmapping(self, key: str, mapping: SimpleMapping): ...

    @abstractmethod
    async def hgetall(self, key: str) -> dict[bytes, bytes]: ...


class Store(ABC):
    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    @abstractmethod
    async def init(self): ...

    @abstractmethod
    async def close(self): ...

    @abstractmethod
    def tx(self) -> StoreSession: ...


def key(jurisdiction_id: str, case_id: str, category: str) -> str:
    """Generate a key for a redis value.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        category (str): The category of the task.

    Returns:
        str: The key.
    """
    return f"{jurisdiction_id}:{case_id}:{category}"
