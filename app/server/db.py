from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import BINARY, DateTime, LargeBinary, String
from sqlalchemy.types import Enum as SQLEnum
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

BaseT = TypeVar("BaseT", bound="Base")

str_256 = Annotated[str, 256]
str_4096 = Annotated[str, 4096]

JobStatus = Enum("JobStatus", "queued processing success error")

CallbackStatus = Enum("CallbackStatus", "pending success error")

TaskState = Enum("TaskState", "unclaimed claimed")


def nowts() -> datetime:
    """Return the current datetime with the timezone set to UTC."""
    return datetime.now(timezone.utc)


def primary_key() -> bytes:
    """Generate a UUIDv7 primary key."""
    return uuid7().bytes


class Base(AsyncAttrs, DeclarativeBase):
    type_annotation_map = {
        UUID: BINARY(16),
        datetime: DateTime(timezone=True),
        str_256: String(256),
        str_4096: String(4096),
        bytes: LargeBinary,
        JobStatus: SQLEnum(JobStatus),
        CallbackStatus: SQLEnum(CallbackStatus),
        TaskState: SQLEnum(TaskState),
    }

    @classmethod
    async def get_by_id(
        cls: Type[BaseT], session: AsyncSession, id: UUID
    ) -> Optional[BaseT]:
        """Get a record by its ID."""
        q = select(cls).filter(cls.id == id)
        result = await session.execute(q)
        return result.scalar_one_or_none()


class Exposure(Base):
    __tablename__ = "exposure"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)
