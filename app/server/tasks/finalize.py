import json

from pydantic import BaseModel

from ..config import config
from ..db import DocumentStatus
from ..generated.models import Document
from .callback import CallbackTaskResult
from .queue import ProcessingError, queue
from .serializer import register_type


class FinalizeTaskResult(BaseModel):
    jurisdiction_id: str
    case_id: str
    document_id: str
    document: Document | None
    errors: list[ProcessingError] = []


register_type(FinalizeTaskResult)


@queue.task(
    task_track_started=True,
    task_time_limit=30,
    task_soft_time_limit=25,
    max_retries=3,
    retry_backoff=True,
    autoretry_for=(Exception,),
)
def finalize(callback_result: CallbackTaskResult) -> FinalizeTaskResult:
    """Finalize the redaction process."""
    format_result = callback_result.formatted
    if config.experiments.enabled:
        with config.experiments.store.driver.sync_session() as session:
            status = DocumentStatus(
                jurisdiction_id=format_result.jurisdiction_id,
                case_id=format_result.case_id,
                document_id=format_result.document_id,
                status="ERROR" if format_result.errors else "COMPLETE",
                error=format_errors(format_result.errors),
            )
            session.add(status)
            session.commit()

    return FinalizeTaskResult(
        document=format_result.document,
        jurisdiction_id=format_result.jurisdiction_id,
        case_id=format_result.case_id,
        document_id=format_result.document_id,
        errors=format_result.errors,
    )


def format_errors(errors: list[ProcessingError]) -> str | None:
    if not errors:
        return None
    return json.dumps([err.model_dump() for err in errors])
