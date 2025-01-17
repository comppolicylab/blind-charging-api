from pathlib import Path
from typing import Literal, TypedDict

import tomllib
from opentelemetry.metrics import get_meter


class WorkersData(TypedDict):
    total: int
    local: int
    healthy: list[str]
    unhealthy: list[str]


class TasksData(TypedDict):
    active: int
    scheduled: int
    reserved: int


class ProcessesData(TypedDict):
    total: int
    idle: int
    expected: int


class HealthCheckData(TypedDict):
    """Aggregated status of Celery workers.

    See:
    https://docs.celeryq.dev/en/v5.3.6/reference/celery.app.control.html#celery.app.control.Control.inspect
    """

    status: Literal["ok"] | Literal["error"]
    error: str | None
    host: str
    workers: WorkersData
    tasks: TasksData
    memory_usage: int
    processes: ProcessesData


def _get_version() -> str:
    """Get the currenet app version from pyprpoject.toml."""
    pyproject = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
    toml = tomllib.loads(pyproject.read_text())
    return toml["tool"]["poetry"]["version"]


class CeleryCustomHealthMetrics:
    def __init__(self):
        self.meter = get_meter(__name__, _get_version())

        self.active_tasks = self.meter.create_gauge(
            "celery.queue.tasks.active",
            "Number of active tasks in the queue",
            "tasks",
        )
        self.scheduled_tasks = self.meter.create_gauge(
            "celery.queue.tasks.scheduled",
            "Number of scheduled tasks in the queue",
            "tasks",
        )
        self.reserved_tasks = self.meter.create_gauge(
            "celery.queue.tasks.reserved",
            "Number of reserved tasks in the queue",
            "tasks",
        )
        self.num_workers = self.meter.create_gauge(
            "celery.workers.count",
            "Number of workers in the pool",
            "workers",
        )
        self.num_unhealthy_workers = self.meter.create_gauge(
            "celery.workers.count.unhealthy",
            "Number of unhealthy workers in the pool",
            "workers",
        )
        self.num_processes = self.meter.create_gauge(
            "celery.workers.processes",
            "Number of worker processes in the pool",
            "processes",
        )
        self.idle_processes = self.meter.create_gauge(
            "celery.workers.processes.idle",
            "Number of idle worker processes in the pool",
            "processes",
        )
        self.expected_processes = self.meter.create_gauge(
            "celery.workers.processes.expected",
            "Number of expected worker processes in the pool",
            "processes",
        )
        self.memory_usage = self.meter.create_gauge(
            "celery.workers.memory",
            "Memory usage of the worker processes",
            "bytes",
        )

    def report(self, data: HealthCheckData):
        self.active_tasks.set(data["tasks"]["active"])
        self.scheduled_tasks.set(data["tasks"]["scheduled"])
        self.reserved_tasks.set(data["tasks"]["reserved"])
        self.num_workers.set(data["workers"]["total"])
        self.num_unhealthy_workers.set(len(data["workers"]["unhealthy"]))
        self.num_processes.set(data["processes"]["total"])
        self.idle_processes.set(data["processes"]["idle"])
        self.expected_processes.set(data["processes"]["expected"])
        self.memory_usage.set(data["memory_usage"])


class CeleryCustomCounter:
    def init(self):
        self.meter = get_meter(__name__, _get_version())
        self.job_counter = self.meter.create_counter(
            "celery.queue.job",
            "Total number of jobs (=task groups) processed",
            "jobs",
        )
        self.callback_counter = self.meter.create_counter(
            "celery.queue.callback",
            "Total number of callbacks",
            "callbacks",
        )
        self.task_retry_counter = self.meter.create_counter(
            "celery.queue.task.retry",
            "Total number of task retries",
            "retries",
        )
        self.task_attempt_counter = self.meter.create_counter(
            "celery.queue.task.attempt",
            "Total number of task attempts",
            "tasks",
        )
        self.task_complete_counter = self.meter.create_counter(
            "celery.queue.task.complete",
            "Total number of task completions",
            "tasks",
        )

    def record_job(self, success: bool):
        self.job_counter.add(1, {"success": success})

    def record_callback(self, success: bool):
        self.callback_counter.add(1, {"success": success})

    def record_retry(self, name: str, type_: str, attempts: int):
        self.task_retry_counter.add(
            1, {"name": name, "type": type_, "attempts": attempts}
        )

    def record_attempt(self, name: str):
        self.task_attempt_counter.add(1, {"name": name})

    def record_complete(self, name: str, success: bool, error: str | None = None):
        self.task_complete_counter.add(
            1, {"name": name, "success": success, "error": error}
        )


celery_counters = CeleryCustomCounter()


def record_task_failure(self, exc, *args, **kwargs):
    exc_type = "UnknownException"
    try:
        exc_type = exc.__class__.__name__
    except Exception:
        pass
    celery_counters.record_complete(self.name, False, exc_type)


def record_task_start(self, *args, **kwargs):
    celery_counters.record_attempt(self.name)


def record_task_success(self, *args, **kwargs):
    celery_counters.record_complete(self.name, True)
