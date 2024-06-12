import asyncio
import logging

import typer

from .config import config
from .db_ops import init_db

logger = logging.getLogger(__name__)

cli = typer.Typer()


@cli.command()
def create_db(wipe: bool = False, alembic_config: str = "alembic.ini") -> None:
    """Ensure the database exists.

    If `wipe` is True, drop the database first.

    Args:
        wipe (bool): Whether to drop the database first.
    """
    from_scratch = asyncio.run(config.db.driver.is_blank_slate())

    # Don't touch the DB if it already exists. See `migrate_db` for that.
    if not from_scratch and not wipe:
        logger.warning(
            "Database already exists and `--wipe` was not specified."
            " Use `migrate_db` to update the database to the latest revision."
        )
        return

    asyncio.run(init_db(drop_first=wipe))

    # Stamp the revision as current so that future migrations will work.
    # Careful that if the database already exists, it should *not* be stamped,
    # since the revision probably won't be "head." It's important that we
    # catch this condition above and return early!
    logger.info("Stamping database revision as current")
    config.db.driver.alembic.stamp("head")

    logger.info("Database created successfully")


@cli.command()
def migrate_db(revision: str = "head", downgrade: bool = False) -> None:
    """Run the database migrations."""
    if downgrade:
        logger.info("Downgrading database to revision %s", revision)
        config.db.driver.alembic.downgrade(revision)
    else:
        logger.info("Upgrading database to revision %s", revision)
        config.db.driver.alembic.upgrade(revision)
    logger.info("Database migrations complete")


if __name__ == "__main__":
    cli()
