import asyncio
import json
import logging

from celery.canvas import Signature
from celery.result import AsyncResult
from pydantic import BaseModel

from app.func import allf

from ..case import CaseStore
from ..case_helper import save_document_sync, save_retry_state_sync, summarize_state
from ..config import config
from ..db import DocumentStatus
from ..generated.models import OutputFormat, RedactionTarget
from .callback import CallbackTaskResult, get_result_sync
from .fetch import inline_document_bytes
from .metrics import (
    celery_counters,
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, get_result, queue
from .serializer import register_type

logger = logging.getLogger(__name__)


class FinalizeTask(BaseModel):
    jurisdiction_id: str
    case_id: str
    subject_ids: list[str] = []
    renderer: OutputFormat

    def s(self) -> Signature:
        return finalize.s(self)


class FinalizeTaskResult(BaseModel):
    jurisdiction_id: str
    case_id: str
    document_id: str
    errors: list[ProcessingError] = []
    next_task_id: str | None = None


register_type(FinalizeTask)
register_type(FinalizeTaskResult)


@queue.task(
    task_track_started=True,
    task_time_limit=30,
    task_soft_time_limit=25,
    max_retries=3,
    retry_backoff=True,
    autoretry_for=(Exception,),
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def finalize(
    callback_result: CallbackTaskResult, params: FinalizeTask
) -> FinalizeTaskResult:
    """Finalize the redaction process."""
    format_result = callback_result.formatted

    # Start from whatever the upstream pipeline reported.
    final_errors: list[ProcessingError] = list(format_result.errors)

    # If the pipeline thinks it succeeded, verify that the redacted document
    # is actually retrievable from the result store. The chain can be
    # "successful" while the result doc is absent (e.g. a Redis write that
    # silently failed earlier, or the key was evicted under memory pressure
    # before finalize ran). Surfacing this here keeps the experiments DB and
    # the poll API in agreement: both will report ERROR with a clear cause
    # instead of disagreeing about whether the document exists.
    if not final_errors:
        try:
            doc = get_result_sync(
                format_result.jurisdiction_id,
                format_result.case_id,
                format_result.document_id,
            )
        except Exception as e:
            logger.exception("Failed to verify presence of redaction result in store")
            final_errors.append(
                ProcessingError.from_exception("finalize.verify_result", e)
            )
        else:
            if doc is None:
                final_errors.append(
                    ProcessingError(
                        message=(
                            "Redaction pipeline reported success but the "
                            "redacted document was not found in the result "
                            "store. The result may have failed to persist "
                            "or has been evicted/expired from cache before "
                            "finalize ran."
                        ),
                        task="finalize.verify_result",
                        exception="MissingResultDocument",
                    )
                )

    celery_counters.record_job(bool(final_errors))

    if config.experiments.enabled:
        with config.experiments.store.driver.sync_session() as session:
            status = DocumentStatus(
                jurisdiction_id=format_result.jurisdiction_id,
                case_id=format_result.case_id,
                document_id=format_result.document_id,
                status="ERROR" if final_errors else "COMPLETE",
                error=format_errors(final_errors),
            )
            session.add(status)
            session.commit()

    # Queue up the next document for processing now, if there is one.
    next_task: AsyncResult | None = None
    next_object = get_next_object_sync(
        format_result.jurisdiction_id, format_result.case_id
    )
    if next_object:
        from .controller import create_document_redaction_task

        # Keep inline (BASE64/TEXT) payloads out of the broker on the iterative
        # multi-object path too: persist the content to the blob store and pass
        # only its id into the next chain (mirrors the API handler).
        prefetched_storage_id: str | None = None
        inline_bytes = inline_document_bytes(next_object.document)
        if inline_bytes is not None:
            prefetched_storage_id = save_document_sync(inline_bytes)
            del inline_bytes

        new_chain = create_document_redaction_task(
            params.jurisdiction_id,
            params.case_id,
            params.subject_ids,
            next_object,
            renderer=params.renderer,
            prefetched_storage_id=prefetched_storage_id,
        )
        if not new_chain:
            # Unclear why we would get here, since we've verified that
            # there are more objects to redact
            raise RuntimeError("Failed to create redaction task")

        next_task = new_chain.apply_async()

        # Save the new task to the store for tracking
        save_doc_task_sync(
            params.jurisdiction_id,
            params.case_id,
            next_object.document.root.documentId,
            next_task,
        )

    return FinalizeTaskResult(
        jurisdiction_id=format_result.jurisdiction_id,
        case_id=format_result.case_id,
        document_id=format_result.document_id,
        errors=final_errors,
        next_task_id=str(next_task) if next_task else None,
    )


def format_errors(errors: list[ProcessingError]) -> str | None:
    if not errors:
        return None
    return json.dumps([err.model_dump() for err in errors])


def get_next_object_sync(jurisdiction_id: str, case_id: str) -> RedactionTarget | None:
    """Get the next objects to redact for a case.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        RedactionTarget: The next object to redact, or None.
    """

    async def _get_objects() -> RedactionTarget | None:
        logging.debug(f"Getting next object for {jurisdiction_id}:{case_id} ...")
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)
                doc_tasks = await cs.get_doc_tasks()
                logging.debug(f"Found {len(doc_tasks)} existing task(s).")

                while True:
                    next_object = await cs.pop_object()
                    if not next_object:
                        logging.debug("No more objects to check, all done processing.")
                        return None
                    # Validate that the next object needs to be redacted.
                    existing_tasks = doc_tasks.get(next_object.document.root.documentId)
                    if not existing_tasks:
                        logging.debug(
                            f"Found next object for {jurisdiction_id}:{case_id}: "
                            f"{next_object.document.root.documentId}"
                        )
                        return next_object

                    summary = summarize_state([get_result(t) for t in existing_tasks])
                    if summary.simple_state == "FAILURE":
                        logging.debug(
                            f"Found failed task for {jurisdiction_id}:{case_id}: "
                            f"{next_object.document.root.documentId}. Retrying."
                        )
                        return next_object
                    else:
                        logging.debug(
                            f"Found existing tasks for {jurisdiction_id}:{case_id}: "
                            f"{next_object.document.root.documentId} "
                            f"({summary.simple_state}). Skipping."
                        )

    return asyncio.run(_get_objects())


def save_doc_task_sync(
    jurisdiction_id: str, case_id: str, doc_id: str, task: AsyncResult
) -> None:
    """Save the task ID for a document to the store

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        doc_id (str): The document ID.
        task (AsyncResult): The task.
    """

    async def _save_id() -> None:
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)
                return await cs.save_doc_task(doc_id, task)

    return asyncio.run(_save_id())
