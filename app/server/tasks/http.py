"""HTTP server to expose health check endpoint while worker is running."""

import logging
import re
import socket
from contextlib import asynccontextmanager
from typing import Literal, Tuple, TypedDict

import psutil
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..bg import BackgroundServer
from ..config import config
from .metrics import CeleryCustomHealthMetrics, HealthCheckData
from .queue import queue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(api: FastAPI):
    async with config.metrics.driver:
        api.state.metrics = CeleryCustomHealthMetrics()
        yield


app = FastAPI(lifespan=lifespan)


_X_TO_BYTES = {
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
    "PB": 1024**5,
    "B": 1,
}

_BYTES_PATTERN = pattern = re.compile(
    r"^([\d,\.]+)\s?(B|KB|MB|GB|TB|PB)$", re.IGNORECASE
)


def _human_size_to_bytes(human: str) -> int:
    """Convert human-readable bytes to bytes."""
    match = _BYTES_PATTERN.match(human)

    if not match:
        raise ValueError(f"Could not parse {human} as bytes.")

    size_s, unit_s = match.groups()
    size = float(size_s.replace(",", ""))

    try:
        conversion = _X_TO_BYTES[unit_s.upper()]
        return int(size * conversion)
    except KeyError as e:
        raise ValueError(
            f"Could not parse {human} as bytes - unknown unit {unit_s}."
        ) from e


def _get_open_file_count() -> Tuple[int, int]:
    """Get the number of open files.

    Returns:
        Tuple[int, int]: The number of open files in current process, and for user.
    """
    cur_proc = psutil.Process()
    cur_proc_open_files = len(cur_proc.open_files())
    user_open_files = 0

    for proc in psutil.process_iter():
        try:
            if proc.username() == cur_proc.username():
                user_open_files += len(proc.open_files())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return (cur_proc_open_files, user_open_files)


def get_full_health_report() -> HealthCheckData:
    """Assemble all the relevant health info we can."""
    inspection = queue.control.inspect()

    # Count the number of tasks in each state.
    active_tasks = inspection.active() or {}
    scheduled_tasks = inspection.scheduled() or {}
    reserved_tasks = inspection.reserved() or {}
    active_count = sum(len(tasks) for tasks in active_tasks.values())
    scheduled_count = sum(len(tasks) for tasks in scheduled_tasks.values())
    reserved_count = sum(len(tasks) for tasks in reserved_tasks.values())

    # Check memory usage
    mem = inspection.memsample() or {}
    total_mem_bytes = sum(_human_size_to_bytes(v) for v in mem.values())

    # Check workers
    stats = inspection.stats() or {}
    processes_count = 0
    expected_processes_count = 0
    for worker_stats in stats.values():
        pool = worker_stats.get("pool", {})
        processes = pool.get("processes", [])
        processes_count += len(processes)
        expected_processes_count += pool.get("max-concurrency", 0)

    # Check basic worker responsiveness
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

    proc_open_files, user_open_files = _get_open_file_count()

    return {
        "host": local,
        "workers": {
            "total": len(health),
            "local": len(unhealthy) + len(healthy),
            "healthy": healthy,
            "unhealthy": unhealthy,
        },
        "tasks": {
            "active": active_count,
            "scheduled": scheduled_count,
            "reserved": reserved_count,
        },
        "processes": {
            "total": processes_count,
            "idle": processes_count - active_count,
            "expected": expected_processes_count,
        },
        "fs": {
            "proc_open_files": proc_open_files,
            "user_open_files": user_open_files,
        },
        "memory_usage": total_mem_bytes,
        "status": "ok" if healthy else "error",
        "error": None if healthy else "No healthy workers found",
    }


def report_full_health(app: FastAPI) -> None:
    """Report full health status."""
    data = get_full_health_report()
    app.state.metrics.report(data)
    logger.debug("Reported health metrics")


@app.get("/health/full")
def health_full(request: Request) -> JSONResponse:
    """Full health report.

    This is generally too much info to return for the basic infra health check.
    It will time out and the health check will fail.

    Intended for manual inspection of the workers.

    If configured, this endpoint will also report metrics to the configured
    metrics driver.

    Returns:
        JSONResponse: The `HealthCheckResponse` with a status of either 500 or 200.
    """
    data = get_full_health_report()

    request.app.state.metrics.report(data)
    status_code = 200 if data["status"] == "ok" else 500
    return JSONResponse(
        status_code=status_code,
        content=data,
    )


@app.get("/health")
def health(request: Request) -> JSONResponse:
    """Health check endpoint.

    Returns the health status of the workers.

    There ought to be at least one healthy worker on the local machine
    if this server is running. So if this does not hold, we return a 500 error.

    Note that as long as there is one healthy worker locally, the server is
    considered healthy, even if there are some unhealthy ones.

    If configured, this endpoint will also report metrics to the configured
    metrics driver.

    Returns:
        JSONResponse: The `HealthCheckResponse` with a status of either 500 or 200.
    """
    data = {
        "status": "ok",
    }

    # Check basic worker responsiveness
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

    status_code = 200

    if not healthy:
        logger.error("No healthy workers found.")
        status_code = 500
        data["error"] = (
            f"No healthy workers found; {len(unhealthy)} unhealthy workers found."
        )
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
    srv = BackgroundServer(cfg)
    srv.add_periodic_task(60, report_full_health)
    return srv
