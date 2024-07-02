from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional, Type, TypeVar

from sqlalchemy import ForeignKey, select, update
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.functions import count
from sqlalchemy.types import BINARY, DateTime, LargeBinary, String
from sqlalchemy.types import Enum as SQLEnum
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

from .config import config

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


class SubjectFile(Base):
    __tablename__ = "subject_file"

    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subject.id"), primary_key=True)
    subject: Mapped["Subject"] = relationship()
    file_id: Mapped[UUID] = mapped_column(ForeignKey("file.id"), primary_key=True)
    file: Mapped["File"] = relationship()
    role: Mapped[str_256]
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class File(Base):
    __tablename__ = "file"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    external_id: Mapped[str_256] = mapped_column(unique=True)
    jurisdiction_id: Mapped[str_256]
    case_id: Mapped[str_256]
    subjects: AssociationProxy[List["Subject"]] = association_proxy(
        "subject_files", "subject"
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


class Subject(Base):
    __tablename__ = "subject"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    external_id: Mapped[str_256] = mapped_column(unique=True)
    files: AssociationProxy[List[File]] = association_proxy("subject_files", "file")
    aliases: Mapped[List["Alias"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Alias(Base):
    __tablename__ = "alias"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    primary: Mapped[bool] = mapped_column(default=False)
    subject_id: Mapped[str_256] = mapped_column(ForeignKey("subject.id"))
    subject: Mapped["Subject"] = relationship(back_populates="aliases")
    title: Mapped[str_256] = mapped_column(nullable=True)
    first_name: Mapped[str_256] = mapped_column(nullable=True)
    middle_name: Mapped[str_256] = mapped_column(nullable=True)
    last_name: Mapped[str_256] = mapped_column(nullable=True)
    suffix: Mapped[str_256] = mapped_column(nullable=True)
    nickname: Mapped[str_256] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="jobs", lazy="subquery")
    status: Mapped[JobStatus] = mapped_column(default=JobStatus.queued)
    error: Mapped[str_256] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)

    @classmethod
    async def start(cls, session: AsyncSession, job_id: UUID) -> None:
        """Start a job."""
        q = (
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.processing, error=None)
        )
        await session.execute(q)

    @classmethod
    async def success(cls, session: AsyncSession, job_id: UUID) -> None:
        """Complete a job."""
        job = await Job.get_by_id(session, job_id)
        if job is None:
            raise ValueError("Job not found.")
        job.status = JobStatus.success
        job.task.state = TaskState.unclaimed
        job.error = None

    @classmethod
    async def set_error(cls, session: AsyncSession, job_id: UUID, error: str) -> None:
        """Complete a job with an error."""
        job = await Job.get_by_id(session, job_id)
        if job is None:
            raise ValueError("Job not found.")
        job.error = error
        job.status = JobStatus.error
        job.task.state = TaskState.unclaimed
        job.task.retry_after = nowts() + timedelta(seconds=config.task.retry_interval)


class Callback(Base):
    __tablename__ = "callback"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.id"))
    task: Mapped["Task"] = relationship(back_populates="callbacks", lazy="subquery")
    status: Mapped[CallbackStatus] = mapped_column(default=CallbackStatus.pending)
    response: Mapped[str_4096] = mapped_column(nullable=True)
    response_code: Mapped[int] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)

    @classmethod
    async def complete(
        cls, session: AsyncSession, cb_id: UUID, response_code: int, response_body: str
    ) -> None:
        """Complete a callback."""
        cb = await cls.get_by_id(session, cb_id)
        if cb is None:
            raise ValueError("Callback not found.")
        if response_code >= 200 and response_code < 300:
            cb.status = CallbackStatus.success
        else:
            cb.status = CallbackStatus.error
        cb.task.state = TaskState.unclaimed
        cb.response_code = response_code
        cb.response = response_body


class Task(Base):
    __tablename__ = "task"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    redaction_id: Mapped[UUID] = mapped_column(ForeignKey("redaction.id"))
    redaction: Mapped["Redaction"] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    jobs: Mapped[List["Job"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="subquery"
    )
    state: Mapped[TaskState] = mapped_column(default=TaskState.unclaimed)
    callback_url: Mapped[str_4096] = mapped_column(nullable=True)
    callbacks: Mapped[List["Callback"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)
    retry_after: Mapped[datetime] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=True)

    @classmethod
    async def next_pending_task(self, session: AsyncSession) -> Optional["Task"]:
        """Find the oldest task that doesn't have a completed job."""
        # Find the oldest task that either doesn't have a job, or has a job that is
        # not in a 'success' state. For retrying jobs, only select tasks that have
        # not reached the maximum number of retries and have waited the appropriate
        # amount of time before retrying.
        q = (
            select(Task, count(Job.id).label("job_count"))
            .where(Task.state == TaskState.unclaimed)
            .where((Task.retry_after.is_(None)) | (Task.retry_after <= nowts()))
            .join(Job, isouter=True)
            .group_by(Task.id)
            .having(count(Task.jobs) < config.task.max_retries)
            .having(~Task.jobs.any(Job.status == JobStatus.success))
            .order_by(Task.created_at.asc())
            .limit(1)
        )
        result = await session.execute(q)
        return result.unique().scalar_one_or_none()

    @classmethod
    async def next_callback_task(self, session: AsyncSession) -> Optional["Task"]:
        """Find the oldest task that needs a callback executed."""
        q = (
            select(Task, count(Callback.id).label("callback_count"))
            .where(Task.state == TaskState.unclaimed, Task.callback_url.is_not(None))
            .where((Task.retry_after.is_(None)) | (Task.retry_after <= nowts()))
            .join(Callback, isouter=True)
            .group_by(Task.id)
            .having(count(Task.callbacks) < config.task.max_retries)
            .having(~Task.callbacks.any(Callback.status == CallbackStatus.success))
            .order_by(Task.created_at.asc())
            .limit(1)
        )
        result = await session.execute(q)
        return result.unique().scalar_one_or_none()

    async def claim(self, session: AsyncSession) -> None:
        """Claim this task for processing."""
        q = (
            update(Task)
            .where(Task.id == self.id, Task.state == TaskState.unclaimed)
            .values(state=TaskState.claimed)
        )
        result = await session.execute(q)
        if result.rowcount != 1:
            raise ValueError("Task already claimed.")

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

    async def get_redaction(self, session: AsyncSession) -> Redaction:
        """Return the redaction for this task."""
        q = select(Redaction).filter(Redaction.id == self.redaction_id)
        result = await session.execute(q)
        return result.scalar_one()
