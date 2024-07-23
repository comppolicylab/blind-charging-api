import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Type, TypeVar

from glowplug import DbDriver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.types import BINARY, NVARCHAR, DateTime, String
from sqlalchemy.types import Enum as SQLEnum
from typing_extensions import Annotated
from uuid_utils import UUID, uuid7

logger = logging.getLogger(__name__)

BaseT = TypeVar("BaseT", bound="Base")

str_256 = Annotated[str, 256]
str_4096 = Annotated[str, 4096]
# 4 MiB is an effectively unlimited text field
# SQLAlchemy will use a vendor-specific type for this, such as LONGTEXT for MySQL
text = Annotated[str, 4_194_304]

ReviewType = Enum("ReviewType", "blind final")

Decision = Enum(
    "Decision",
    "disqualify decline charge charge_likely charge_maybe decline_likely decline_maybe",
)

Disqualifier = Enum(
    "Disqualifier",
    "assigned_to_unblind case_type_ineligible prior_knowledge_bias "
    "narrative_incomplete redaction_missing redaction_illegible other",
)


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
        str_256: String(255),
        str_4096: String(4095),
        ReviewType: SQLEnum(ReviewType),
        Decision: SQLEnum(Decision),
        Disqualifier: SQLEnum(Disqualifier),
        text: String(4_194_303).with_variant(NVARCHAR("max"), "mssql"),
    }

    @classmethod
    async def get_by_id(
        cls: Type[BaseT], session: AsyncSession, id: UUID
    ) -> Optional[BaseT]:
        """Get a record by its ID."""
        q = select(cls).filter(cls.id == id)
        result = await session.execute(q)
        return result.scalar_one_or_none()


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "feature"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    entity_type: Mapped[str_256] = mapped_column(index=True)
    entity_id: Mapped[str_4096] = mapped_column(index=True)
    feature: Mapped[str_256] = mapped_column(index=True)
    variant: Mapped[str_256] = mapped_column(index=True)
    value: Mapped[text] = mapped_column()
    ts: Mapped[datetime] = mapped_column()
    event_id: Mapped[str_256] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Exposure(Base):
    __tablename__ = "exposure"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    jurisdiction_id: Mapped[str_256] = mapped_column()
    case_id: Mapped[str_256] = mapped_column()
    subject_id: Mapped[str_256] = mapped_column()
    document_ids: Mapped[str_4096] = mapped_column()
    reviewer_id: Mapped[str_256] = mapped_column()
    review_type: Mapped[ReviewType] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class Outcome(Base):
    __tablename__ = "outcome"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    jurisdiction_id: Mapped[str_256] = mapped_column()
    case_id: Mapped[str_256] = mapped_column()
    subject_id: Mapped[str_256] = mapped_column()
    reviewer_id: Mapped[str_256] = mapped_column()
    document_ids: Mapped[str_4096] = mapped_column()
    review_type: Mapped[ReviewType] = mapped_column()
    decision: Mapped[Decision] = mapped_column()
    explanation: Mapped[text] = mapped_column(nullable=True)
    disqualifier: Mapped[Disqualifier] = mapped_column(nullable=True)
    additional_evidence: Mapped[text] = mapped_column(nullable=True)
    page_open_ts: Mapped[datetime] = mapped_column()
    decision_ts: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


class DocumentStatus(Base):
    __tablename__ = "document_status"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=primary_key)
    jurisdiction_id: Mapped[str_256] = mapped_column()
    case_id: Mapped[str_256] = mapped_column()
    document_id: Mapped[str_256] = mapped_column()
    status: Mapped[str_256] = mapped_column()
    error: Mapped[str_4096] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=nowts)
    updated_at: Mapped[datetime] = mapped_column(default=nowts, onupdate=nowts)


async def init_db(driver: DbDriver, drop_first: bool = False) -> None:
    """Initialize the database and its tables.

    Args:
        driver (DbDriver): The database driver.
        drop_first (bool): Whether to drop the tables first
    """
    if not await driver.exists():
        logger.info("No database exists, creating a new one")
        await driver.create()
    else:
        logger.info("Database already exists")

    # Create the database
    if drop_first:
        logger.info("Re-creating database tables")
    else:
        logger.info("Creating database tables")
    await driver.init(Base, drop_first=drop_first)
