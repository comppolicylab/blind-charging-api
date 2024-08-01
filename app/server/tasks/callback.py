import asyncio
import logging

import requests
from pydantic import BaseModel

from ..case import CaseStore
from ..config import config
from ..generated.models import (
    MaskedSubject,
    RedactionResult,
    RedactionResultError,
    RedactionResultSuccess,
)
from .format import FormatTaskResult
from .queue import queue
from .serializer import register_type

logger = logging.getLogger(__name__)


class CallbackTask(BaseModel):
    callback_url: str | None = None


class CallbackTaskResult(BaseModel):
    status_code: int
    response: str | None = None
    formatted: FormatTaskResult


register_type(CallbackTask)
register_type(CallbackTaskResult)


_callback_timeout = config.queue.task.callback_timeout_seconds


@queue.task(
    task_track_started=True,
    task_time_limit=_callback_timeout + 10,
    task_soft_time_limit=_callback_timeout,
    max_retries=5,
    retry_backoff=True,
    autoretry_for=(Exception,),
)
def callback(
    format_result: FormatTaskResult, params: CallbackTask
) -> CallbackTaskResult:
    """Post callbacks to the client as requested."""
    if params.callback_url:
        try:
            masked_subjects = get_masks_sync(
                format_result.jurisdiction_id, format_result.case_id
            )
        except Exception:
            logger.exception("Error getting masked subjects")
            masked_subjects = []

        if format_result.redact_error or not format_result.document:
            body = RedactionResult(
                RedactionResultError(
                    jurisdictionId=format_result.jurisdiction_id,
                    caseId=format_result.case_id,
                    inputDocumentId=format_result.document_id,
                    maskedSubjects=masked_subjects,
                    error=format_result.redact_error
                    or "Unknown error redacting document",
                    status="ERROR",
                )
            )
        else:
            body = RedactionResult(
                RedactionResultSuccess(
                    jurisdictionId=format_result.jurisdiction_id,
                    caseId=format_result.case_id,
                    inputDocumentId=format_result.document_id,
                    maskedSubjects=masked_subjects,
                    redactedDocument=format_result.document,
                    status="COMPLETE",
                )
            )
        response = requests.post(
            params.callback_url,
            json=body.model_dump(mode="json"),
        )
        response.raise_for_status()

        return CallbackTaskResult(
            status_code=response.status_code,
            response=response.text,
            formatted=format_result,
        )

    return CallbackTaskResult(
        status_code=0,
        response="[nothing to do]",
        formatted=format_result,
    )


def get_masks_sync(jurisdiction_id: str, case_id: str) -> list[MaskedSubject]:
    """Get the masked subjects for a case.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        list[MaskedSubject]: The masked subjects.
    """

    async def _get_masks_with_store() -> list[MaskedSubject]:
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)
                return await cs.get_masked_names()

    return asyncio.run(_get_masks_with_store())
