import enum
from datetime import datetime, timezone
from typing import List

from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import BINARY, DateTime, Enum, LargeBinary, String
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

str_256 = Annotated[str, 256]

JobStatus = enum.Enum("JobStatus", "queued processing finished error")

CallbackStatus = enum.Enum("CallbackStatus", "waiting success failure")


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
        JobStatus: Enum(JobStatus),
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
    task: Mapped["Task"] = relationship(back_populates="file")
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


class Job(Base):
    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="jobs")
    status: Mapped[JobStatus] = mapped_column(default=JobStatus.queued)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Callback(Base):
    __tablename__ = "callback"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="callbacks")
    status: Mapped[CallbackStatus] = mapped_column(default=CallbackStatus.waiting)
    response: Mapped[str_256] = mapped_column(nullable=True)
    response_code: Mapped[int] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Task(Base):
    __tablename__ = "task"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"))
    file: Mapped["File"] = relationship(
        back_populates="task", cascade="all, delete-orphan", single_parent=True
    )
    jobs: Mapped[List["Job"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    callback_url: Mapped[str] = mapped_column(nullable=True)
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
        return await session.execute(q).scalar_one()
