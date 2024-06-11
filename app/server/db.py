from datetime import datetime, timezone
from typing import List

from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import BINARY, DateTime, LargeBinary, String
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

str_256 = Annotated[str, 256]


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
        bytes: LargeBinary,
    }

    async def save(self, session: AsyncSession, commit: bool = False) -> None:
        session.add(self)
        if commit:
            await session.commit()


class File(Base):
    __tablename__ = "file"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    external_id: Mapped[str_256] = mapped_column(unique=True)
    jurisdiction_id: Mapped[str_256]
    case_id: Mapped[str_256]
    defendants: Mapped[List["DefendantFile"]] = relationship()
    redactions: Mapped[List["Redaction"]] = relationship(back_populates="file")
    content: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Redaction(Base):
    __tablename__ = "redaction"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"))
    file: Mapped["File"] = relationship(back_populates="redactions")
    content: Mapped[bytes]
    status: Mapped[str_256]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class DefendantFile(Base):
    __tablename__ = "defendant_file"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"))
    file: Mapped["File"] = relationship(back_populates="defendants")
    defendant_id: Mapped[str_256]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Events(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    event_type: Mapped[str_256]
    event_data: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=nowts)


class ProcessingQueue(Base):
    __tablename__ = "processing_queue"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"))
    status: Mapped[str_256] = mapped_column(default="queued")
    job_id: Mapped[UUID] = mapped_column(nullable=True)
    callback_url: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)
