import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from glowplug import DbDriver
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy.exc import SQLAlchemyError

import alembic.util.exc

from .config import RdbmsConfig, config
from .db import clear_invalid_revision
from .features import init_gater
from .generated.main import app as generated_app
from .meta import meta_router
from .time import utcnow

logger = logging.getLogger(__name__)


async def ensure_db(store: RdbmsConfig) -> DbDriver:
    """Check the database and apply migrations if possible.

    Args:
        store (RdbmsConfig): The database configuration.

    Returns:
        DbDriver: The database driver.
    """
    if await store.driver.is_blank_slate():
        logger.error(
            "Database is not initialized. "
            "Please set up the database before running the app."
        )
        raise SystemExit(1)

    logger.info("Applying any pending database migrations ...")
    # Note that failures here are *not* fatal, so that the app can still try to run.
    try:
        store.driver.alembic.upgrade("head")
    except alembic.util.exc.CommandError as e:
        if "Can't locate revision" in str(e):
            logger.info("Existing revision not found. Trying to auto-remediate.")
            try:
                clear_invalid_revision(store.driver)
                store.driver.alembic.upgrade("head")
                logger.info("Success! Database is now at the latest revision.")
            except Exception as exc:
                logger.error("Failed to apply database migrations: %s", exc)
                logger.error(
                    "The database migrations are in an invalid state. "
                    "Please fix manually."
                )
    except (NameError, ValueError, KeyError, RuntimeError, SQLAlchemyError):
        logger.error(
            "Failed to apply database migrations. "
            "Seems like the migration is invalid."
        )
        raise
    except Exception as e:
        logger.exception("Failed to apply database migrations: %s", e)
        if store.driver.alembic.current() is None:
            logger.error(
                "Database probably was not stamped properly. Assuming it's head "
                "and retrying ..."
            )
            try:
                store.driver.alembic.stamp("head")
                store.driver.alembic.upgrade("head")
                logger.info("Success! Database is now at the latest revision.")
            except Exception as exc:
                logger.error("Failed to apply database migrations: %s", exc)
                logger.error(
                    "The database migrations are in an invalid state. "
                    "Please fix manually."
                )
        else:
            logger.error("Failed to apply database migrations: %s", e)
            logger.error("Trying to fix the underlying issue and retry.")
    return store.driver


@asynccontextmanager
async def lifespan(api: FastAPI):
    """Setup and teardown logic for the server."""
    logger.info("Starting up ...")
    gater = init_gater()
    api.state.gater = gater
    api.state.startup_time = utcnow()

    db = await ensure_db(config.experiments.store)

    async with config.queue.store.driver() as store, config.metrics.driver:
        api.state.queue_store = store
        api.state.db = db
        yield

    logger.info("Shutting down ...")
    gater.stop()
    logger.info("Bye!")


app = FastAPI(
    lifespan=lifespan,
    title="Race Blind Charging",
    description="See /api/v1/docs for more information.",
    contact={"name": "Joe Nudell", "email": "jnudell@hks.harvard.edu"},
    license={"name": "MIT License", "url": "https://opensource.org/license/mit/"},
)

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


# Instrument the API with metrics.
# The base `app` with health checks (etc.) is *not* instrumented!
# This keeps the metrics focused on client-facing APIs.
# (We will get other alerts if the health checks fail.)
FastAPIInstrumentor().instrument_app(generated_app)

app.include_router(meta_router)
app.mount("/api/v1", generated_app)
