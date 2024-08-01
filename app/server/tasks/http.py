"""HTTP server to expose health check endpoint while worker is running."""

import logging

import uvicorn
from fastapi import FastAPI

from ..bg import BackgroundServer
from .queue import queue

logger = logging.getLogger(__name__)

app = FastAPI()


@app.get("/health")
def health() -> dict:
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
