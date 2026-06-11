import logging

from celery import chain

from ..generated.models import ExtractionTarget, OutputFormat, RedactionTarget
from .callback import CallbackTask
from .extract import ExtractionTask
from .extract_callback import ExtractionCallbackTask
from .fetch import (
    FetchTask,
    UnidentifiedFetchTask,
    make_bcstore_document,
    make_unidentified_bcstore_document,
)
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
    prefetched_storage_id: str | None = None,
) -> chain | None:
    """Create database objects representing a document redaction task.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        subject_ids (list[str]): The IDs of the subjects to redact.
        object (RedactionTarget): The document to redact.
        renderer (OutputFormat, optional): The output format for the redacted document.
        prefetched_storage_id (str, optional): Blob-store id of an inline payload
            that was persisted before dispatch. When provided, the document
            content is not carried inline through the broker.

    Returns:
        chain: The Celery chain representing the redaction pipeline.
    """
    document_id = object.document.root.documentId
    if prefetched_storage_id is not None:
        # Inline content was pre-staged in the blob store; reference it as an
        # internal bcstore link instead of carrying the payload through the
        # broker.
        fetch_task = FetchTask(
            document=make_bcstore_document(document_id, prefetched_storage_id),
        )
    else:
        fetch_task = FetchTask(document=object.document)
    return chain(
        fetch_task.s(),
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


def create_document_extraction_task(
    token: str,
    object: ExtractionTarget,
    prefetched_storage_id: str | None = None,
) -> chain:
    """Create celery chain for extraction on one document.

    Args:
        token (str): The document token / id.
        object (ExtractionTarget): The document to extract.
        prefetched_storage_id (str, optional): Blob-store id of an inline payload
            that was persisted before dispatch. When provided, the document
            content is not carried inline through the broker.
    """
    if prefetched_storage_id is not None:
        fetch_task = UnidentifiedFetchTask(
            document=make_unidentified_bcstore_document(prefetched_storage_id),
            document_id=token,
        )
    else:
        fetch_task = UnidentifiedFetchTask(
            document=object.document,
            document_id=token,
        )
    return chain(
        fetch_task.s(),
        ExtractionTask(
            document_id=token,
        ).s(),
        ExtractionCallbackTask(
            callback_url=str(object.callbackUrl) if object.callbackUrl else None
        ).s(),
    )
