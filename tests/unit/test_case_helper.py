from unittest.mock import AsyncMock, MagicMock

import pytest

from app.server.case_helper import summarize_state
from app.server.handlers.redaction import _get_doc_result
from app.server.tasks import ProcessingError


def _result(state: str, name: str, result_value=None) -> MagicMock:
    """Build a stand-in for a celery ``AsyncResult``.

    ``summarize_state`` only reads ``.state`` and ``.name`` from each entry,
    so a MagicMock is sufficient and avoids needing a live Celery backend.
    """
    m = MagicMock()
    m.state = state
    m.name = name
    m.result = result_value
    return m


def test_summarize_all_success_prefers_last_task():
    """When every task in the chain succeeds, the final task wins.

    This is the case that previously caused ``FetchTaskResult`` (with an
    empty ``errors`` list) to mask real errors recorded on later tasks.
    """
    tasks = [
        _result("SUCCESS", "fetch"),
        _result("SUCCESS", "redact"),
        _result("SUCCESS", "format"),
        _result("SUCCESS", "callback"),
        _result("SUCCESS", "finalize"),
    ]
    summary = summarize_state(tasks)
    assert summary.simple_state == "SUCCESS"
    assert summary.dominant_task_name == "finalize"
    assert summary.result is tasks[-1]


def test_summarize_failure_dominates_regardless_of_position():
    tasks = [
        _result("SUCCESS", "fetch"),
        _result("FAILURE", "redact"),
        _result("PENDING", "format"),
        _result("PENDING", "callback"),
        _result("PENDING", "finalize"),
    ]
    summary = summarize_state(tasks)
    assert summary.simple_state == "FAILURE"
    assert summary.dominant_task_name == "redact"
    assert summary.result is tasks[1]


def test_summarize_started_dominates_pending():
    tasks = [
        _result("SUCCESS", "fetch"),
        _result("STARTED", "redact"),
        _result("PENDING", "format"),
        _result("PENDING", "callback"),
        _result("PENDING", "finalize"),
    ]
    summary = summarize_state(tasks)
    assert summary.simple_state == "STARTED"
    assert summary.dominant_task_name == "redact"


def test_summarize_retry_dominates_started():
    tasks = [
        _result("RETRY", "fetch"),
        _result("STARTED", "redact"),
        _result("PENDING", "format"),
    ]
    summary = summarize_state(tasks)
    assert summary.simple_state == "RETRY"
    assert summary.dominant_task_name == "fetch"


def test_summarize_all_pending_prefers_last_task():
    """All ties resolve to the latest task in the chain.

    The user-visible status is still ``PENDING``; only the diagnostic
    ``dominant_task_name`` changes.
    """
    tasks = [
        _result("PENDING", "fetch"),
        _result("PENDING", "redact"),
        _result("PENDING", "format"),
        _result("PENDING", "callback"),
        _result("PENDING", "finalize"),
    ]
    summary = summarize_state(tasks)
    assert summary.simple_state == "PENDING"
    assert summary.dominant_task_name == "finalize"


def test_summarize_empty_list_returns_unknown():
    summary = summarize_state([])
    assert summary.simple_state == "UNKNOWN"
    assert summary.dominant_task_name == "<unknown>"
    assert summary.result is None


def test_summarize_single_success():
    tasks = [_result("SUCCESS", "fetch")]
    summary = summarize_state(tasks)
    assert summary.simple_state == "SUCCESS"
    assert summary.dominant_task_name == "fetch"
    assert summary.result is tasks[0]


# --- _get_doc_result: SUCCESS-but-missing-doc messaging -----------------------
#
# These tests cover the handler branch where Celery reports SUCCESS but
# `get_result_doc` returns None. The user-facing error message should
# clearly indicate that the result has likely expired or been evicted,
# rather than the older "no specific errors were recorded" wording which
# read like a silent pipeline bug.


@pytest.mark.asyncio
async def test_get_doc_result_missing_doc_reports_expiry_message():
    finalize_result = MagicMock()
    # No `errors` attribute -> getattr default kicks in -> empty list.
    del finalize_result.errors

    finalize_async = MagicMock()
    finalize_async.state = "SUCCESS"
    finalize_async.name = "finalize"
    finalize_async.result = finalize_result

    store = MagicMock()
    store.get_result_doc = AsyncMock(return_value=None)

    with pytest.MonkeyPatch.context() as mp:
        # Patch `get_result` to return our synthetic AsyncResult.
        mp.setattr(
            "app.server.handlers.redaction.get_result",
            lambda task_id: finalize_async,
        )
        result = await _get_doc_result(
            store=store,
            jurisdiction_id="jur1",
            case_id="case1",
            doc_id="doc1",
            task_ids=["finalize-task-id"],
            masked_subjects=[],
        )

    body = result.root
    assert body.status == "ERROR"
    assert "expired" in body.error.lower() or "evicted" in body.error.lower()
    assert "resubmit" in body.error.lower()


@pytest.mark.asyncio
async def test_get_doc_result_missing_doc_prefers_recorded_errors():
    """When the dominant task actually recorded errors, those take
    precedence over the expiry-style fallback message.
    """
    finalize_result = MagicMock()
    finalize_result.errors = [
        ProcessingError(
            message="Boom",
            task="format.save_result",
            exception="RuntimeError",
        )
    ]

    finalize_async = MagicMock()
    finalize_async.state = "SUCCESS"
    finalize_async.name = "finalize"
    finalize_async.result = finalize_result

    store = MagicMock()
    store.get_result_doc = AsyncMock(return_value=None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.server.handlers.redaction.get_result",
            lambda task_id: finalize_async,
        )
        result = await _get_doc_result(
            store=store,
            jurisdiction_id="jur1",
            case_id="case1",
            doc_id="doc1",
            task_ids=["finalize-task-id"],
            masked_subjects=[],
        )

    body = result.root
    assert body.status == "ERROR"
    # Recorded error wins; expiry fallback should not appear.
    assert "expired" not in body.error.lower()
    assert "format.save_result" in body.error
