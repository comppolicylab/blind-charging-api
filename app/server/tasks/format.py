import asyncio
import base64
import json

from azure.storage.blob import BlobClient
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import AnyUrl, BaseModel

from app.func import allf

from ..case import CaseStore
from ..case_helper import get_document_sync, save_retry_state_sync
from ..config import config
from ..generated.models import (
    Content,
    DocumentContent,
    DocumentJSON,
    DocumentLink,
    OutputDocument,
    OutputFormat,
)
from .metrics import (
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, queue
from .redact import RedactionTaskResult
from .serializer import register_type

logger = get_task_logger(__name__)


class FormatTask(BaseModel):
    target_blob_url: str | None = None

    def s(self) -> Signature:
        return format.s(self)


class FormatTaskResult(BaseModel):
    jurisdiction_id: str
    case_id: str
    document_id: str
    errors: list[ProcessingError] = []


register_type(FormatTask)
register_type(FormatTaskResult)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=30,
    task_soft_time_limit=25,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def format(
    self,
    redact_result: RedactionTaskResult,
    params: FormatTask,
) -> FormatTaskResult:
    """Format redaction into a Document type."""
    document: OutputDocument | None = None

    if redact_result.errors:
        # If there are errors from the redact task, pass through.
        return FormatTaskResult(
            jurisdiction_id=redact_result.jurisdiction_id,
            case_id=redact_result.case_id,
            document_id=redact_result.document_id,
            errors=redact_result.errors,
        )
    try:
        if params.target_blob_url:
            document = OutputDocument(
                root=DocumentLink(
                    documentId=redact_result.document_id,
                    attachmentType="LINK",
                    url=AnyUrl(params.target_blob_url),
                )
            )
            content = get_document_sync(redact_result.file_storage_id)
            if not content:
                raise ValueError("Missing redacted content")

            # TODO - we can validate the blob URL to guard against accidental inputs
            write_to_azure_blob_url(params.target_blob_url, content)
        else:
            document = format_document(params, redact_result)

        save_result_sync(
            redact_result.jurisdiction_id,
            redact_result.case_id,
            redact_result.document_id,
            document,
        )

        return FormatTaskResult(
            jurisdiction_id=redact_result.jurisdiction_id,
            case_id=redact_result.case_id,
            document_id=redact_result.document_id,
            errors=redact_result.errors,
        )
    except Exception as e:
        if format.request.retries < format.max_retries:
            logger.warning(f"Format task failed: {e}, will be retried.")
            raise format.retry() from e
        else:
            logger.error(f"Format task failed for {redact_result.document_id}")
            logger.exception(e)
            return FormatTaskResult(
                jurisdiction_id=redact_result.jurisdiction_id,
                case_id=redact_result.case_id,
                document_id=redact_result.document_id,
                errors=[
                    *redact_result.errors,
                    ProcessingError.from_exception("format", e),
                ],
            )


def format_document(
    format_task: FormatTask, redaction: RedactionTaskResult
) -> OutputDocument:
    """Format a redacted document for the API response.

    Args:
        format_task (FormatTask): The format task.
        redaction (RedactionTaskResult): The redaction results.

    Returns:
        OutputDocument: The formatted document.
    """
    document_id = redaction.document_id
    if format_task.target_blob_url:
        return OutputDocument(
            root=DocumentLink(
                documentId=document_id,
                attachmentType="LINK",
                url=AnyUrl(format_task.target_blob_url),
            )
        )
    else:
        content = get_document_sync(redaction.file_storage_id)
        if not content:
            raise ValueError("No redacted content")
        if redaction.renderer == OutputFormat.JSON:
            json_content = json.loads(content)
            return OutputDocument(
                root=DocumentJSON(
                    documentId=document_id,
                    attachmentType="JSON",
                    content=Content(
                        original=json_content["original"],
                        redacted=json_content["redacted"],
                        annotations=json_content.get("annotations", []),
                    ),
                )
            )
        return OutputDocument(
            root=DocumentContent(
                documentId=document_id,
                attachmentType="BASE64",
                content=base64.b64encode(content).decode("utf-8"),
            )
        )


def write_to_azure_blob_url(sas_url: str, content: bytes):
    """Write content to an Azure blob URL.

    Args:
        sas_url (str): The Azure blob SAS URL.
        content (bytes): The content to write.
    """
    client = BlobClient.from_blob_url(blob_url=sas_url)
    client.upload_blob(content)


def save_result_sync(
    jurisdiction_id: str, case_id: str, doc_id: str, document: OutputDocument
) -> str:
    """Save the formatted document in the queue's store.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        doc_id (str): The document ID.
        document (Document): The formatted document.

    Returns:
        ID in the store where the content was saved.
    """

    async def _save():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)
                return await cs.save_result_doc(doc_id, document)

    return asyncio.run(_save())
