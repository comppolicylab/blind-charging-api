import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import config
from .generated import app as generated_app

logger = logging.getLogger(__name__)


async def lifespan(api: FastAPI):
    """Setup and teardown logic for the server."""
    async with config.store.driver() as store:
        api.state.store = store
        yield


app = FastAPI(lifespan=lifespan)
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
async def begin_store_session(request: Request, call_next):
    """Begin a transaction in the store for each request.

    The session is committed if the request is successful, and rolled back if
    an exception is raised.
    """
    async with request.app.state.store.tx() as tx:
        request.state.tx = tx
        return await call_next(request)


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


if config.debug:
    # For testing local callbacks during development we can specify our own
    # echo endpoint. This will be removed in production.
    @app.post("/echo")
    async def echo(request: Request):
        """Echo the request."""
        logger.debug(f"Received request: {request.method} {request.url.path}")
        print("\n\n\n", await request.json(), "\n\n\n")
        return "ok"


app.mount("/api/v1", generated_app)
