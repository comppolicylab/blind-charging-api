import json
import logging

import requests
from celery.canvas import Signature
from pydantic import BaseModel

from app.func import allf

from ..case_helper import save_retry_state_sync
from ..config import config
from ..generated.models import (
    ExtractionResultCompleted,
    ExtractionResultError,
    ExtractionResultSuccess,
)
from .extract import ExtractionTaskResult
from .metrics import (
    celery_counters,
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, queue
from .serializer import register_type

logger = logging.getLogger(__name__)


class ExtractionCallbackTask(BaseModel):
    callback_url: str | None = None

    def s(self) -> Signature:
        return extraction_callback.s(self)


class ExtractionCallbackTaskResult(BaseModel):
    status_code: int
    response: str | None = None
    extracted: ExtractionTaskResult


register_type(ExtractionCallbackTask)
register_type(ExtractionCallbackTaskResult)


_callback_timeout = config.queue.task.callback_timeout_seconds


@queue.task(
    task_track_started=True,
    task_time_limit=_callback_timeout + 10,
    task_soft_time_limit=_callback_timeout,
    max_retries=5,
    retry_backoff=True,
    autoretry_for=(Exception,),
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def extraction_callback(
    extract_result: ExtractionTaskResult, params: ExtractionCallbackTask
) -> ExtractionCallbackTaskResult:
    """Post extraction callback if requested."""
    if params.callback_url:
        body = build_callback_body(extract_result)
        response = requests.post(
            params.callback_url,
            json=body.model_dump(mode="json"),
        )
        try:
            response.raise_for_status()
            celery_counters.record_callback(True)
        except Exception:
            celery_counters.record_callback(False)
            raise

        return ExtractionCallbackTaskResult(
            status_code=response.status_code,
            response=response.text,
            extracted=extract_result,
        )

    return ExtractionCallbackTaskResult(
        status_code=0,
        response="[nothing to do]",
        extracted=extract_result,
    )


def build_callback_body(result: ExtractionTaskResult) -> ExtractionResultCompleted:
    """Build callback body for extraction status."""
    if result.errors or not result.extracted_report:
        return ExtractionResultCompleted(
            ExtractionResultError(
                error=format_errors(result.errors),
                status="ERROR",
            )
        )

    return ExtractionResultCompleted(
        ExtractionResultSuccess(
            extractedReport=result.extracted_report,
            status="COMPLETE",
        )
    )


def format_errors(errors: list[ProcessingError]) -> str:
    if not errors:
        return json.dumps(
            [
                {
                    "message": "Unknown error",
                    "task": "unknown",
                    "exception": "UnknownException",
                }
            ]
        )
    return json.dumps([err.model_dump() for err in errors])
