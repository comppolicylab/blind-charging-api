import asyncio
import logging

import typer

from app.server.config import config
from app.server.db import init_db
from app.server.tasks import get_liveness_app, queue

from .provision import init_provision_cli

logger = logging.getLogger(__name__)

_cli = typer.Typer()


@_cli.command()
def create_client(name: str) -> None:
    """Create a new client in the database.

    Args:
        name (str): The name of the client (will become Client ID)

    Output:
        Client ID and Client Secret
    """
    driver = config.authentication.driver
    create = getattr(driver, "register_client", None)
    if not create:
        logger.error("Client registration is not supported by the current authn driver")
        return

    async def _run():
        async with config.authentication.store.driver.async_session() as session:
            response = await create(session, name)
            await session.commit()
            return response

    client = asyncio.run(_run())
    print(
        "\n\nSuccessfully created a client. "
        "Store the Client ID and Client Secret in a safe place. "
        "We will not be able to retrieve the secret again.\n"
    )
    print(f"\t  Client Name:\t{name}")
    print(f"\t    Client ID:\t{client.client_id}")
    print(f"\tClient Secret:\t{client.client_secret}")


@_cli.command()
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


@_cli.command()
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


@_cli.command()
def worker(
    liveness_host: str = "127.0.0.1", liveness_port: int = 8001, monitor: bool = True
) -> None:
    """Run the Celery worker.

    This command also starts an HTTP liveness probe on port 8001.
    """
    with get_liveness_app(host=liveness_host, port=liveness_port).run_in_thread():
        queue.Worker(task_events=monitor).start()


init_provision_cli(_cli)


def cli():
    print("""\

██████╗ ██████╗  ██████╗               ██████╗██╗     ██╗
██╔══██╗██╔══██╗██╔════╝              ██╔════╝██║     ██║
██████╔╝██████╔╝██║         █████╗    ██║     ██║     ██║
██╔══██╗██╔══██╗██║         ╚════╝    ██║     ██║     ██║
██║  ██║██████╔╝╚██████╗              ╚██████╗███████╗██║
╚═╝  ╚═╝╚═════╝  ╚═════╝               ╚═════╝╚══════╝╚═╝

""")
    _cli()
