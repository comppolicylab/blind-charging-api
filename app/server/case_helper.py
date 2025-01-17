import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from billiard.einfo import ExceptionInfo
from celery.result import AsyncResult

from .case import CaseStore
from .config import config
from .tasks.metrics import celery_counters


def get_document_sync(file_storage_id: str | None) -> bytes:
    """Get the document content from the store.

    Args:
        file_storage_id: The ID in the store where the content was saved.

    Returns:
        bytes: The content.
    """
    if not file_storage_id:
        return b""

    async def _get():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                return await CaseStore.get(tx, file_storage_id)

    return asyncio.run(_get())


def save_document_sync(file_bytes: bytes) -> str:
    """Save the fetched document in the queue's store.

    Args:
        file_bytes: Content to save.

    Returns:
        ID in the store where the content was saved.
    """

    async def _save():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                return await CaseStore.save_blob(tx, file_bytes)

    return asyncio.run(_save())


def save_retry_state_sync(
    self,
    exc: Exception | None,
    task_id: str,
    args: list,
    kwargs: dict,
    einfo: ExceptionInfo,
) -> None:
    """Store the retry state in the results store."""
    exc_type = "UnknownException"

    try:
        exc_type = exc.__class__.__name__
    except Exception:
        pass

    celery_counters.record_retry(self.name, exc_type, self.request.retries)

    async def _store():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                ts = await tx.time()
                dt = datetime.fromtimestamp(ts, tz=UTC)
                data = json.dumps(
                    {
                        "name": self.name,
                        "exception": str(exc),
                        "attempts": self.request.retries + 1,
                        "max_attempts": self.max_retries + 1,
                        "last_failure": dt.isoformat(),
                    }
                )
                await CaseStore.save_key(tx, f"{task_id}:retry", data.encode("utf-8"))

    asyncio.run(_store())


async def get_retry_state(task_id: str) -> str | None:
    """Get the retry state from the results store."""
    async with config.queue.store.driver() as store:
        async with store.tx() as tx:
            s = await CaseStore.get(tx, f"{task_id}:retry")
            if s:
                return s.decode("utf-8")
            return None


@dataclass
class StateSummary:
    simple_state: str
    dominant_task_name: str
    result: AsyncResult


def _inspect_celery_state(result: AsyncResult) -> int:
    """Determine relative priority of a task result state.

    Args:
        result: The task result.

    Returns:
        int: The significance of the state.
    """
    if result.state == "FAILURE":
        return 5
    elif result.state == "RETRY":
        return 4
    elif result.state == "STARTED":
        return 3
    elif result.state == "PENDING":
        return 2
    elif result.state != "SUCCESS":
        return 1
    else:  # SUCCESS
        return 0


def summarize_state(task_results: list[AsyncResult]) -> StateSummary:
    """Summarize the state of a group of tasks.

    Args:
        task_results: The list of task results.

    Returns:
        StateSummary: The summary.
    """
    simple_state = "UNKNOWN"
    max_significance = -1
    dominant_task_name = "<unknown>"
    result = None
    for tr in task_results:
        significance = _inspect_celery_state(tr)
        if significance > max_significance:
            max_significance = significance
            simple_state = tr.state
            dominant_task_name = tr.name
            result = tr
    return StateSummary(simple_state, dominant_task_name, result)
