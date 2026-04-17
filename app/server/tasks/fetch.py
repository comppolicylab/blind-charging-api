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
    document: InputDocument

    def s(self) -> Signature:
        return fetch.s(self)


class ExtractionFetchTask(BaseModel):
    document: InputDocument
    document_id: str

    def s(self) -> Signature:
        return fetch_extraction.s(self)


class FetchTaskResult(BaseModel):
    document_id: str
    file_storage_id: str | None = None
    errors: list[ProcessingError] = []


register_type(FetchTask)
register_type(ExtractionFetchTask)
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
    try:
        content = fetch_document_content(params.document)

        return FetchTaskResult(
            document_id=params.document.root.documentId,
            file_storage_id=save_document_sync(content),
        )
    except Exception as e:
        if self.request.retries < self.max_retries:
            logger.warning(f"Fetch task failed: {e}, will be retried.")
            return self.retry(exc=e)
        else:
            logger.error(f"Fetch task failed for {params.document.root.documentId}")
            logger.exception(e)
            return FetchTaskResult(
                document_id=params.document.root.documentId,
                errors=[ProcessingError.from_exception("fetch", e)],
            )


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
def fetch_extraction(self, params: ExtractionFetchTask) -> FetchTaskResult:
    """Fetch the content of an extraction document."""
    try:
        content = fetch_document_content(params.document)
        return FetchTaskResult(
            document_id=params.document_id,
            file_storage_id=save_document_sync(content),
        )
    except Exception as e:
        if self.request.retries < self.max_retries:
            logger.warning(f"Extraction fetch task failed: {e}, will be retried.")
            return self.retry(exc=e)
        else:
            logger.error(f"Extraction fetch task failed for {params.document_id}")
            logger.exception(e)
            return FetchTaskResult(
                document_id=params.document_id,
                errors=[ProcessingError.from_exception("fetch", e)],
            )


def fetch_document_content(document: InputDocument) -> bytes:
    """Fetch bytes from a supported input document type."""
    match document.root.attachmentType:
        case "LINK":
            url = cast(DocumentLink, document.root).url
            response = requests.get(
                str(url),
                timeout=config.queue.task.link_download_timeout_seconds,
            )
            response.raise_for_status()
            return response.content
        case "TEXT":
            return cast(DocumentText, document.root).content.encode("utf-8")
        case "BASE64":
            content = cast(DocumentContent, document.root).content
            return base64.b64decode(content)
        case _:
            raise ValueError(
                f"Unsupported attachment type: {document.root.attachmentType}"
            )
