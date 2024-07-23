import asyncio
import logging

import typer

from .config import config
from .db import init_db
from .tasks import get_liveness_app, queue

logger = logging.getLogger(__name__)

cli = typer.Typer()


@cli.command()
def create_db(wipe: bool = False, alembic_config: str = "alembic.ini") -> None:
    """Ensure the database exists.
    If `wipe` is True, drop the database first.
    Args:
        wipe (bool): Whether to drop the database first.
    """
    driver = config.experiments.store.driver
    from_scratch = asyncio.run(driver.is_blank_slate())

    # Don't touch the DB if it already exists. See `migrate_db` for that.
    if not from_scratch and not wipe:
        logger.warning(
            "Database already exists and `--wipe` was not specified."
            " Use `migrate_db` to update the database to the latest revision."
        )
        return

    asyncio.run(init_db(driver, drop_first=wipe))

    # Stamp the revision as current so that future migrations will work.
    # Careful that if the database already exists, it should *not* be stamped,
    # since the revision probably won't be "head." It's important that we
    # catch this condition above and return early!
    logger.info("Stamping database revision as current")
    driver.alembic.stamp("head")

    logger.info("Database created successfully")


@cli.command()
def migrate_db(revision: str = "head", downgrade: bool = False) -> None:
    """Run the database migrations."""
    driver = config.experiments.store.driver
    if downgrade:
        logger.info("Downgrading database to revision %s", revision)
        driver.alembic.downgrade(revision)
    else:
        logger.info("Upgrading database to revision %s", revision)
        driver.alembic.upgrade(revision)
    logger.info("Database migrations complete")


@cli.command()
def worker(
    liveness_host: str = "127.0.0.1", liveness_port: int = 8001, monitor: bool = True
) -> None:
    """Run the Celery worker.

    This command also starts an HTTP liveness probe on port 8001.
    """
    with get_liveness_app(host=liveness_host, port=liveness_port).run_in_thread():
        queue.Worker(task_events=monitor).start()


if __name__ == "__main__":
    cli()
