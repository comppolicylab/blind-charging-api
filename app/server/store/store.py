import json
from abc import ABC, abstractmethod
from typing import Mapping, Type, TypeVar, Union

from pydantic import BaseModel

SimpleType = Union[bytes, memoryview, str, int, float]

SimpleMapping = Mapping[str | bytes, bytes | float | int | str]

SomeModel = TypeVar("SomeModel", bound=BaseModel)


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

    async def getdict(self, key: str) -> dict[str, SimpleType] | None:
        """Get a dictionary of values.

        Args:
            key (str): The key of the set.

        Returns:
            dict[str, SimpleType] | None: The value.
        """
        value = await self.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def getmodel(self, cls: Type[SomeModel], key: str) -> SomeModel | None:
        """Dequeue a Pydantic model.

        Args:
            cls (Type[T]): The Pydantic model class.
            key (str): The key of the queue.

        Returns:
            T | None: The dequeued value.
        """
        value = await self.getdict(key)
        if value is None:
            return None
        return cls.model_validate(value)

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
        await self.sadddict(key, value.model_dump(mode="json"))

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
        await self.setdict(key, value.model_dump(mode="json"))

    @abstractmethod
    async def hsetmapping(self, key: str, mapping: SimpleMapping): ...

    @abstractmethod
    async def hgetall(self, key: str) -> dict[bytes, bytes]: ...

    @abstractmethod
    async def expire_at(self, key: str, expire_at: int): ...

    @abstractmethod
    async def enqueue(self, key: str, value: str): ...

    @abstractmethod
    async def dequeue(self, key: str) -> bytes | None: ...

    async def enqueue_model(self, key: str, value: BaseModel):
        """Enqueue a Pydantic model.

        Args:
            key (str): The key of the queue.
            value (BaseModel): The value to enqueue.
        """
        await self.enqueue_dict(key, value.model_dump(mode="json"))

    async def enqueue_dict(self, key: str, value: dict[str, SimpleType]):
        """Enqueue a dictionary.

        Args:
            key (str): The key of the queue.
            value (dict[str, SimpleType]): The value to enqueue.
        """
        await self.enqueue(key, json.dumps(value, sort_keys=True))

    async def dequeue_dict(self, key: str) -> dict[str, SimpleType] | None:
        """Dequeue a dictionary.

        Args:
            key (str): The key of the queue.

        Returns:
            dict[str, SimpleType] | None: The dequeued value.
        """
        value = await self.dequeue(key)
        if value is None:
            return None
        return json.loads(value)

    async def dequeue_model(self, cls: Type[SomeModel], key: str) -> SomeModel | None:
        """Dequeue a Pydantic model.

        Args:
            cls (Type[T]): The Pydantic model class.
            key (str): The key of the queue.

        Returns:
            T | None: The dequeued value.
        """
        value = await self.dequeue_dict(key)
        if value is None:
            return None
        return cls.model_validate(value)

    @abstractmethod
    async def time(self) -> int: ...


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
