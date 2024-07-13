from celery import Celery
from celery.result import AsyncResult

from ..config import config

queue = Celery(
    "app.server.tasks",
    broker=config.broker.url,
    backend=config.store.url,
)


def get_result(task_id: str) -> AsyncResult:
    """Get the async result for a task."""
    return AsyncResult(task_id, app=queue)
