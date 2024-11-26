import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from glowplug import DbDriver
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .config import RdbmsConfig, config
from .db import init_db
from .features import init_gater
from .generated.main import app as generated_app
from .time import utcnow

logger = logging.getLogger(__name__)


async def ensure_db(store: RdbmsConfig, automigrate: bool = False) -> DbDriver:
    """Check the database and apply migrations if possible.

    Args:
        store (RdbmsConfig): The database configuration.
        automigrate (bool): Whether to automatically apply migrations.

    Returns:
        DbDriver: The database driver.
    """
    if await store.driver.is_blank_slate():
        if not automigrate:
            logger.error(
                "Database is not initialized. "
                "Please set up the database before running the app."
            )
            raise SystemExit(1)
        logger.info("Initializing database ...")
        await init_db(store.driver, drop_first=False)
        logger.info("Stamping database revision as current")
        store.driver.alembic.stamp("head")
    else:
        if automigrate:
            logger.info("Applying any pending database migrations ...")
            store.driver.alembic.upgrade("head")
        else:
            logger.warning("Skipping database migration check!")
    return store.driver


@asynccontextmanager
async def lifespan(api: FastAPI):
    """Setup and teardown logic for the server."""
    logger.warning("Starting up ...")
    gater = init_gater()
    api.state.gater = gater

    db = await ensure_db(
        config.experiments.store, automigrate=config.experiments.automigrate
    )

    async with config.queue.store.driver() as store, config.metrics.driver:
        api.state.queue_store = store
        api.state.db = db
        yield

    logger.warning("Shutting down ...")
    gater.stop()
    logger.info("Bye!")


app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor().instrument_app(app)
# Share state between main app and generated app.
generated_app.state = app.state


@generated_app.exception_handler(Exception)
async def handle_exception(request: Request, exc: Exception):
    """Handle exceptions."""
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    else:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )


@generated_app.middleware("http")
async def set_time(request: Request, call_next):
    """Set a function to get the current timestamp."""
    request.state.now = getattr(request.app.state, "now", utcnow)
    return await call_next(request)


@generated_app.middleware("http")
async def begin_store_session(request: Request, call_next):
    """Begin a transaction in the store for each request.

    The session is committed if the request is successful, and rolled back if
    an exception is raised.
    """
    async with request.app.state.queue_store.tx() as store:
        request.state.store = store
        return await call_next(request)


@generated_app.middleware("http")
async def begin_authn_session(request: Request, call_next):
    """Load authentication driver for the request."""
    request.state.authn = config.authentication.driver
    request.state.authn_method = config.authentication.method

    authn_store = getattr(config.authentication, "store", None)
    if authn_store:
        async with authn_store.driver.async_session_with_args(
            pool_pre_ping=True
        )() as session:
            request.state.authn_db = session
            try:
                response = await call_next(request)
                await session.commit()
                return response
            except Exception as e:
                await session.rollback()
                raise e
    else:
        return await call_next(request)


@generated_app.middleware("http")
async def begin_db_session(request: Request, call_next):
    async with request.app.state.db.async_session_with_args(
        pool_pre_ping=True
    )() as session:
        request.state.db = session
        try:
            response = await call_next(request)
            await session.commit()
            return response
        except Exception as e:
            await session.rollback()
            raise e


@generated_app.middleware("http")
async def log_request(request: Request, call_next):
    """Log the request."""
    t0 = time.monotonic()
    try:
        return await call_next(request)
    finally:
        if config.debug:
            client_host = request.client.host if request.client else "[unknown client]"
            t1 = time.monotonic()
            elapsed = t1 - t0
            logger.debug(
                f"{request.method} {request.url.path} " f"{client_host} {elapsed:.2f}s"
            )


if config.debug:
    # For testing local callbacks during development we can specify our own
    # echo endpoint. This will be removed in production.
    @app.post("/echo")
    async def echo(request: Request):
        """Echo the request."""
        logger.debug(f"Received request: {request.method} {request.url.path}")
        print("\n\n\n", await request.json(), "\n\n\n")
        return "ok"

    # For testing file redaction, mount some public document samples.
    app.mount(
        "/sample_data",
        StaticFiles(directory=os.path.join("app", "server", "sample_data")),
        name="sample_data",
    )


app.mount("/api/v1", generated_app)
