import secrets

from fastapi import HTTPException, Request

from ..config import config
from ..generated.models import (
    ExtractionAccepted,
    ExtractionRequest,
    ExtractionResult,
    ExtractionResultError,
    ExtractionResultPending,
    ExtractionResultSuccess,
    ExtractionStatus,
)
from ..tasks import create_document_extraction_task, get_result
from ..tasks.extract_callback import ExtractionCallbackTaskResult
from .redaction import validate_callback_url


def _task_key(token: str) -> str:
    return f"extract:task:{token}"


async def extract_documents(
    *,
    request: Request,
    body: ExtractionRequest,
) -> ExtractionAccepted:
    """Queue extraction tasks and return polling tokens."""
    tokens = list[str]()
    now = await request.state.store.time()
    expires_at = now + config.queue.task.retention_time_seconds

    for doc in body.documents:
        callback_url = str(doc.callbackUrl) if doc.callbackUrl else None
        validate_callback_url(callback_url)

        token = secrets.token_urlsafe(24)
        task_chain = create_document_extraction_task(token, doc)
        task = task_chain.apply_async()

        await request.state.store.set(_task_key(token), task.id)
        await request.state.store.expire_at(_task_key(token), expires_at)
        tokens.append(token)

    return ExtractionAccepted(tokens=tokens)


async def get_extraction_status(*, request: Request, token: str) -> ExtractionStatus:
    """Get extraction result by token."""
    task_id = await request.state.store.get(_task_key(token))
    if not task_id:
        raise HTTPException(status_code=404, detail="Token not found")

    task_result = get_result(task_id.decode("utf-8"))
    state = task_result.state

    if state == "PENDING":
        result = ExtractionResult(
            ExtractionResultPending(
                status="QUEUED",
                statusDetail="Extraction request has been received and is queued.",
            )
        )
    elif state in {"RETRY", "STARTED"}:
        result = ExtractionResult(
            ExtractionResultPending(
                status="PROCESSING",
                statusDetail="Extraction task is currently being processed.",
            )
        )
    elif state == "SUCCESS":
        final_result = task_result.result
        if not isinstance(final_result, ExtractionCallbackTaskResult):
            result = ExtractionResult(
                ExtractionResultError(
                    status="ERROR",
                    error="Unexpected extraction task result type.",
                )
            )
        elif (
            final_result.extracted.errors or not final_result.extracted.extracted_report
        ):
            result = ExtractionResult(
                ExtractionResultError(
                    status="ERROR",
                    error=str(final_result.extracted.errors)
                    or "Unknown extraction error",
                )
            )
        else:
            result = ExtractionResult(
                ExtractionResultSuccess(
                    status="COMPLETE",
                    extractedReport=final_result.extracted.extracted_report,
                )
            )
    elif state == "FAILURE":
        result = ExtractionResult(
            ExtractionResultError(
                status="ERROR",
                error=str(task_result.result),
            )
        )
    else:
        result = ExtractionResult(
            ExtractionResultError(
                status="ERROR",
                error=f"Unknown extraction task state: {state}",
            )
        )

    return ExtractionStatus(token=token, result=result)
