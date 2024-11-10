"""HTTP server to expose health check endpoint while worker is running."""

import logging
import socket
from contextlib import asynccontextmanager
from typing import Literal, TypedDict

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..bg import BackgroundServer
from ..config import config
from .queue import queue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(api: FastAPI):
    async with config.metrics.driver:
        yield


app = FastAPI(lifespan=lifespan)


class HealthCheckResponse(TypedDict):
    """Response from Celery worker.

    See:
    https://docs.celeryq.dev/en/v5.3.6/reference/celery.app.control.html#celery.app.control.Control.ping
    """

    status: Literal["ok"] | Literal["error"]
    error: str | None
    total_workers: int
    local_workers: int
    host: str
    healthy_workers: list[str]
    unhealthy_workers: list[str]


@app.get("/health")
def health() -> JSONResponse:
    """Health check endpoint.

    Returns the health status of the workers.

    There ought to be at least one healthy worker on the local machine
    if this server is running. So if this does not hold, we return a 500 error.

    Note that as long as there is one healthy worker locally, the server is
    considered healthy, even if there are some unhealthy ones.

    The full result can be inspected for remediation if necessary.

    Returns:
        JSONResponse: The `HealthCheckResponse` with a status of either 500 or 200.
    """
    health = queue.control.ping()
    local = socket.gethostname()
    address = f"@{local}"
    healthy: list[str] = []
    unhealthy: list[str] = []
    for workers in health:
        for worker, response in workers.items():
            if not worker.endswith(address):
                continue
            if response.get("ok") == "pong":
                healthy.append(worker)
            else:
                unhealthy.append(worker)

    data = {
        "total_workers": len(health),
        "local_workers": len(unhealthy) + len(healthy),
        "host": local,
        "healthy_workers": healthy,
        "unhealthy_workers": unhealthy,
        "status": "ok",
        "error": None,
    }
    status_code = 200

    if not healthy:
        logger.error("No healthy workers found.")
        status_code = 500
        data["error"] = "No healthy workers found"
        data["status"] = "error"

    return JSONResponse(
        status_code=status_code,
        content=data,
    )


class RawWorkerHealth(TypedDict):
    """Response from Celery worker.

    See:
    https://docs.celeryq.dev/en/v5.3.6/reference/celery.app.control.html#celery.app.control.Control.ping
    """

    ok: Literal["pong"]


class WorkerRawHealthCheckResponse(TypedDict):
    """Health check response from the worker daemon."""

    workers: list[dict[str, RawWorkerHealth]]


@app.get("/health/raw")
def health_raw() -> dict:
    """Call `celery inspect ping` and return the result."""
    return {"workers": queue.control.ping()}


@app.get("/")
def root() -> None:
    """Root endpoint."""
    return None


def get_liveness_app(host: str = "127.0.0.1", port: int = 8001) -> BackgroundServer:
    """Get a background server instance for liveness checks."""
    cfg = uvicorn.Config(app, host=host, port=port)
    return BackgroundServer(cfg)
