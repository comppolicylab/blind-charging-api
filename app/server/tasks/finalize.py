from pydantic import BaseModel

from ..config import config
from ..db import DocumentStatus
from ..generated.models import Document
from .callback import CallbackTaskResult
from .queue import queue
from .serializer import register_type


class FinalizeTaskResult(BaseModel):
    document: Document


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
    if config.experiments.enabled:
        format_result = callback_result.formatted

        with config.experiments.store.driver.sync_session() as session:
            status = DocumentStatus(
                jurisdiction_id=format_result.jurisdiction_id,
                case_id=format_result.case_id,
                document_id=format_result.document.root.documentId,
                status="ERROR" if format_result.redact_error else "COMPLETE",
                error=format_result.redact_error,
            )
            session.add(status)
            session.commit()

    return FinalizeTaskResult(document=format_result.document)
