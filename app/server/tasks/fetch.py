import base64
from typing import cast

import requests
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import BaseModel

from app.func import allf

from ..case_helper import save_document_sync, save_retry_state_sync
from ..config import config
from ..generated.models import (
    DocumentContent,
    DocumentLink,
    DocumentText,
    InputDocument,
    UnidentifiedDocumentContent,
    UnidentifiedDocumentLink,
    UnidentifiedInputDocument,
)
from .metrics import (
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, queue
from .serializer import register_type

logger = get_task_logger(__name__)


class FetchTask(BaseModel):
    # When an inline (BASE64/TEXT) document is persisted to the blob store
    # before dispatch, the heavy payload travels via ``file_storage_id`` instead
    # of being carried inline through the broker. In that case ``document`` is
    # omitted and ``document_id`` identifies the document. For LINK documents the
    # ``document`` is carried through so the worker downloads it directly.
    document: InputDocument | None = None
    document_id: str | None = None
    file_storage_id: str | None = None

    def s(self) -> Signature:
        return fetch.s(self)


class UnidentifiedFetchTask(BaseModel):
    document: UnidentifiedInputDocument | None = None
    document_id: str
    file_storage_id: str | None = None

    def s(self) -> Signature:
        return fetch_unidentified.s(self)


class FetchTaskResult(BaseModel):
    document_id: str
    file_storage_id: str | None = None
    errors: list[ProcessingError] = []


register_type(FetchTask)
register_type(UnidentifiedFetchTask)
register_type(FetchTaskResult)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=config.queue.task.link_download_timeout_seconds + 30,
    task_soft_time_limit=config.queue.task.link_download_timeout_seconds,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def fetch(self, params: FetchTask) -> FetchTaskResult:
    """Fetch the content of a document.

    Args:
        params (FetchTask): The task parameters.

    Returns:
        FetchTaskResult: The task result.
    """
    # Inline payloads (BASE64/TEXT) are persisted to the blob store before
    # dispatch so they don't travel through the broker. When that has happened
    # there's nothing left to fetch -- just pass the storage id downstream.
    if params.file_storage_id is not None:
        return FetchTaskResult(
            document_id=params.document_id or "",
            file_storage_id=params.file_storage_id,
        )
    if params.document is None:
        raise ValueError("FetchTask requires either a document or a file_storage_id")
    return _fetch_and_save(self, params.document.root.documentId, params.document)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=config.queue.task.link_download_timeout_seconds + 30,
    task_soft_time_limit=config.queue.task.link_download_timeout_seconds,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def fetch_unidentified(self, params: UnidentifiedFetchTask) -> FetchTaskResult:
    """Fetch the content of an unidentified input document."""
    if params.file_storage_id is not None:
        return FetchTaskResult(
            document_id=params.document_id,
            file_storage_id=params.file_storage_id,
        )
    if params.document is None:
        raise ValueError(
            "UnidentifiedFetchTask requires either a document or a file_storage_id"
        )
    return _fetch_and_save(self, params.document_id, params.document)


def _fetch_and_save(
    task,
    document_id: str,
    document: InputDocument | UnidentifiedInputDocument,
) -> FetchTaskResult:
    """Fetch document bytes, persist them, and build a task result.

    Shared implementation for the identified and unidentified fetch tasks.
    """
    try:
        content = fetch_document_content(document)
        return FetchTaskResult(
            document_id=document_id,
            file_storage_id=save_document_sync(content),
        )
    except Exception as e:
        if task.request.retries < task.max_retries:
            logger.warning(f"Fetch task failed: {e}, will be retried.")
            return task.retry(exc=e)
        logger.error(f"Fetch task failed for {document_id}")
        logger.exception(e)
        return FetchTaskResult(
            document_id=document_id,
            errors=[ProcessingError.from_exception("fetch", e)],
        )


def inline_document_bytes(
    document: InputDocument | UnidentifiedInputDocument,
) -> bytes | None:
    """Decode an inline (BASE64/TEXT) document payload to raw bytes.

    Returns ``None`` for non-inline attachment types (e.g. LINK), whose bytes
    are not present in the request and should be fetched by the worker instead.

    This lets the API persist inline payloads to the blob store up front so the
    (potentially large) content never has to be serialized into a Celery
    message and round-tripped through the broker.
    """
    match document.root.attachmentType:
        case "BASE64":
            return base64.b64decode(document.root.content)
        case "TEXT":
            return document.root.content.encode("utf-8")
        case _:
            return None


def fetch_document_content(
    document: InputDocument | UnidentifiedInputDocument,
) -> bytes:
    """Fetch bytes from a supported input document type."""
    match document.root.attachmentType:
        case "LINK":
            if isinstance(document, InputDocument):
                url = cast(DocumentLink, document.root).url
            else:
                url = cast(UnidentifiedDocumentLink, document.root).url
            response = requests.get(
                str(url),
                timeout=config.queue.task.link_download_timeout_seconds,
            )
            response.raise_for_status()
            return response.content
        case "TEXT":
            if not isinstance(document, InputDocument):
                raise ValueError("TEXT attachment is not supported for anonymous docs.")
            return cast(DocumentText, document.root).content.encode("utf-8")
        case "BASE64":
            if isinstance(document, InputDocument):
                content = cast(DocumentContent, document.root).content
            else:
                content = cast(UnidentifiedDocumentContent, document.root).content
            return base64.b64decode(content)
        case _:
            raise ValueError(
                f"Unsupported attachment type: {document.root.attachmentType}"
            )
