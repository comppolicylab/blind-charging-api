"""HTTP server to expose health check endpoint while worker is running."""

import contextlib
import logging
import threading
import time
from typing import Generator

import uvicorn
from fastapi import FastAPI

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


# Adapted from: https://bugfactory.io/articles/starting-and-stopping-uvicorn-in-the-background/
class BackgroundServer(uvicorn.Server):
    """A uvicorn server that can be run in a background thread."""

    @contextlib.contextmanager
    def run_in_thread(self) -> Generator:
        thread = threading.Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                time.sleep(0.001)
                pass
            yield
        finally:
            self.should_exit = True
            thread.join()


def get_liveness_app(host: str = "127.0.0.1", port: int = 8001) -> BackgroundServer:
    """Get a background server instance for liveness checks."""
    cfg = uvicorn.Config(app, host=host, port=port)
    return BackgroundServer(cfg)
