import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import typer
from fastapi_cli.cli import dev as _dev
from fastapi_cli.cli import run

from .provision import init_provision_cli

_APP_ROOT = Path(__file__).parent.parent
_APP_PATH = _APP_ROOT / "app" / "server" / "app.py"

logging.basicConfig(level=logging.INFO)
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
    from app.server.config import config

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
    from app.server.config import config
    from app.server.db import init_db

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
    from app.server.config import config

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
    from app.server.tasks import get_liveness_app, queue

    with get_liveness_app(host=liveness_host, port=liveness_port).run_in_thread():
        queue.Worker(task_events=monitor).start()


@_cli.command()
def api(
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int = 1,
    proxy_headers: bool = False,
) -> None:
    """Run the API server.

    Run the API HTTP server on the given host and port.
    """
    run(_APP_PATH, host=host, port=port, workers=workers, proxy_headers=proxy_headers)


@_cli.command()
def dev(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = True,
    config: Optional[str] = None,
) -> None:
    """Run the API server in development mode."""
    if config:
        logger.info("Using config file: %s", config)
        os.environ["CONFIG_PATH"] = config
    elif "CONFIG_PATH" not in os.environ:
        # Try to set a smart default for the config path
        local_config_path = _APP_ROOT / "config.local.toml"
        if local_config_path.exists():
            abs_config_path = str(local_config_path.absolute())
            logger.info("Detected local config file: %s", abs_config_path)
            os.environ["CONFIG_PATH"] = abs_config_path
        else:
            logger.warning("No config file specified. Using default.")
    else:
        logger.info("Using config file: %s", os.environ["CONFIG_PATH"])

    _dev(_APP_PATH, host=host, port=port, reload=reload)


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
