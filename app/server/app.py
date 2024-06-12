import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from .config import config
from .db_ops import init_db
from .generated import app as generated_app

logger = logging.getLogger(__name__)


async def lifespan(_: FastAPI):
    """Setup and teardown logic for the server."""
    if await config.db.driver.is_blank_slate():
        if not config.automigrate:
            logger.error(
                "Database is not initialized. "
                "Please set up the database before running the app."
            )
            raise SystemExit(1)
        logger.info("Initializing database ...")
        await init_db(drop_first=False)
        logger.info("Stamping database revision as current")
        config.db.driver.alembic.stamp("head")
    else:
        logger.info("Applying any pending database migrations ...")
        config.db.driver.alembic.upgrade("head")

    yield


app = FastAPI(lifespan=lifespan)


@generated_app.exception_handler(Exception)
async def handle_exception(request: Request, exc: Exception):
    """Handle exceptions."""
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    elif isinstance(exc, IntegrityError):
        return JSONResponse(
            status_code=400,
            content={"detail": "Record already exists."},
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )


@generated_app.middleware("http")
async def begin_db_session(request: Request, call_next):
    """Begin a database session for each request.

    The session is committed if the request is successful, and rolled back if
    an exception is raised.
    """
    async with config.db.driver.async_session() as session:
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
            t1 = time.monotonic()
            elapsed = t1 - t0
            logger.debug(
                f"{request.method} {request.url.path} "
                f"{request.client.host} {elapsed:.2f}s"
            )


app.mount("/api/v1", generated_app)
