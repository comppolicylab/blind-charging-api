"""HTTP server to expose health check endpoint while worker is running."""

import logging
from typing import Literal, TypedDict

import uvicorn
from fastapi import FastAPI

from ..bg import BackgroundServer
from .queue import queue

logger = logging.getLogger(__name__)

app = FastAPI()


class WorkerHealth(TypedDict):
    """Response from Celery worker.

    See:
    https://docs.celeryq.dev/en/v5.3.6/reference/celery.app.control.html#celery.app.control.Control.ping
    """

    ok: Literal["pong"]


class HealthCheckResponse(TypedDict):
    """Health check response from the worker daemon."""

    workers: list[dict[str, WorkerHealth]]


@app.get("/health")
def health() -> HealthCheckResponse:
    """Health check endpoint."""
    return {"workers": queue.control.ping()}


@app.get("/")
def root() -> None:
    """Root endpoint."""
    return None


def get_liveness_app(host: str = "127.0.0.1", port: int = 8001) -> BackgroundServer:
    """Get a background server instance for liveness checks."""
    cfg = uvicorn.Config(app, host=host, port=port)
    return BackgroundServer(cfg)
