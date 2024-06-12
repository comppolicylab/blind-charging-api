import logging

from .config import config
from .db import Base

logger = logging.getLogger(__name__)


async def init_db(drop_first: bool = False) -> None:
    """Initialize the database and its tables."""
    if not await config.db.driver.exists():
        logger.info("No database exists, creating a new one")
        await config.db.driver.create()
    else:
        logger.info("Database already exists")

    # Create the database
    if drop_first:
        logger.info("Re-creating database tables")
    else:
        logger.info("Creating database tables")
    await config.db.driver.init(Base, drop_first=drop_first)
