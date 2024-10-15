import base64

from azure.storage.blob import BlobClient
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import AnyUrl, BaseModel

from ..generated.models import Document, DocumentContent, DocumentLink
from .queue import ProcessingError, queue
from .redact import RedactionTaskResult
from .serializer import register_type

logger = get_task_logger(__name__)


class FormatTask(BaseModel):
    target_blob_url: str | None = None

    def s(self) -> Signature:
        return format.s(self)


class FormatTaskResult(BaseModel):
    document: Document | None
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
)
def format(
    self,
    redact_result: RedactionTaskResult,
    params: FormatTask,
) -> FormatTaskResult:
    """Format redaction into a Document type."""
    document: Document | None = None

    if redact_result.errors:
        # If there are errors from the redact task, pass through.
        return FormatTaskResult(
            document=None,
            jurisdiction_id=redact_result.jurisdiction_id,
            case_id=redact_result.case_id,
            document_id=redact_result.document_id,
            errors=redact_result.errors,
        )
    try:
        if params.target_blob_url:
            document = Document(
                root=DocumentLink(
                    documentId=redact_result.document_id,
                    attachmentType="LINK",
                    url=AnyUrl(params.target_blob_url),
                )
            )
            if not redact_result.content:
                raise ValueError("Missing redacted content")

            # TODO - we can validate the blob URL to guard against accidental inputs
            write_to_azure_blob_url(params.target_blob_url, redact_result.content)
        else:
            document = format_document(params, redact_result)

        return FormatTaskResult(
            document=document,
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
                document=None,
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
) -> Document:
    """Format a redacted document for the API response.

    Args:
        format_task (FormatTask): The format task.
        redaction (RedactionTaskResult): The redaction results.

    Returns:
        Document: The formatted document.
    """
    document_id = redaction.document_id
    if format_task.target_blob_url:
        return Document(
            root=DocumentLink(
                documentId=document_id,
                attachmentType="LINK",
                url=AnyUrl(format_task.target_blob_url),
            )
        )
    else:
        if not redaction.content:
            raise ValueError("No redacted content")
        return Document(
            root=DocumentContent(
                documentId=document_id,
                attachmentType="BASE64",
                content=base64.b64encode(redaction.content).decode("utf-8"),
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
