from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import BINARY, DateTime, LargeBinary, String
from sqlalchemy.types import Enum as SQLEnum
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

str_256 = Annotated[str, 256]
str_4096 = Annotated[str, 4096]

JobStatus = Enum("JobStatus", "queued processing success error")

CallbackStatus = Enum("CallbackStatus", "pending success error")


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
    }


class File(Base):
    __tablename__ = "file"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    external_id: Mapped[str_256] = mapped_column(unique=True)
    jurisdiction_id: Mapped[str_256]
    case_id: Mapped[str_256]
    defendants: Mapped[List["DefendantFile"]] = relationship(
        cascade="all, delete-orphan"
    )
    redactions: Mapped[List["Redaction"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
    content: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)

    async def latest_redaction(self, session: AsyncSession) -> Optional["Redaction"]:
        """Return the latest redaction for this file."""
        q = (
            select(Redaction)
            .filter(Redaction.file_id == self.id)
            .order_by(Redaction.created_at.desc())
            .limit(1)
        )
        result = await session.execute(q)
        return result.scalar_one_or_none()


class Redaction(Base):
    __tablename__ = "redaction"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"))
    file: Mapped["File"] = relationship(back_populates="redactions", lazy="subquery")
    task: Mapped["Task"] = relationship(back_populates="redaction", lazy="subquery")
    external_link: Mapped[str_4096] = mapped_column(nullable=True)
    content: Mapped[bytes] = mapped_column(nullable=True)
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


class Job(Base):
    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="jobs")
    status: Mapped[JobStatus] = mapped_column(default=JobStatus.queued)
    error: Mapped[str_256] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Callback(Base):
    __tablename__ = "callback"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="callbacks")
    status: Mapped[CallbackStatus] = mapped_column(default=CallbackStatus.pending)
    response: Mapped[str_4096] = mapped_column(nullable=True)
    response_code: Mapped[int] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Task(Base):
    __tablename__ = "task"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    redaction_id: Mapped[UUID] = mapped_column(ForeignKey("redaction.id"))
    redaction: Mapped["Redaction"] = relationship(
        back_populates="task", cascade="all, delete-orphan", single_parent=True
    )
    jobs: Mapped[List["Job"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="subquery"
    )
    callback_url: Mapped[str_4096] = mapped_column(nullable=True)
    callbacks: Mapped[List["Callback"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)
    expires_at: Mapped[datetime] = mapped_column(nullable=True)

    async def latest_job(self, session: AsyncSession) -> Job | None:
        """Return the latest job for this task."""
        q = (
            select(Job)
            .filter(Job.task_id == self.id)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        result = await session.execute(q)
        return result.scalar_one_or_none()
