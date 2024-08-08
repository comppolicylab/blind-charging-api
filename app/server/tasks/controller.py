import logging

from celery import chain

from ..generated.models import OutputFormat, RedactionTarget
from .callback import CallbackTask
from .fetch import FetchTask
from .finalize import FinalizeTask
from .format import FormatTask
from .redact import RedactionTask

logger = logging.getLogger(__name__)


def create_document_redaction_task(
    jurisdiction_id: str,
    case_id: str,
    subject_ids: list[str],
    object: RedactionTarget,
    renderer: OutputFormat = OutputFormat.PDF,
) -> chain | None:
    """Create database objects representing a document redaction task.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        subject_ids (list[str]): The IDs of the subjects to redact.
        object (RedactionTarget): The document to redact.
        renderer (OutputFormat, optional): The output format for the redacted document.

    Returns:
        chain: The Celery chain representing the redaction pipeline.
    """
    return chain(
        FetchTask(
            document=object.document,
        ).s(),
        RedactionTask(
            document_id=object.document.root.documentId,
            jurisdiction_id=jurisdiction_id,
            case_id=case_id,
            renderer=renderer,
        ).s(),
        FormatTask(
            target_blob_url=str(object.targetBlobUrl) if object.targetBlobUrl else None,
        ).s(),
        CallbackTask(
            callback_url=str(object.callbackUrl) if object.callbackUrl else None
        ).s(),
        FinalizeTask(
            jurisdiction_id=jurisdiction_id,
            case_id=case_id,
            subject_ids=subject_ids,
            renderer=renderer,
        ).s(),
    )
