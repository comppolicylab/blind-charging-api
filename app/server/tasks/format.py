import base64

from azure.storage.blob import BlobClient
from pydantic import BaseModel

from ..generated.models import Document, DocumentContent, DocumentLink
from .queue import queue
from .redact import RedactionTaskResult
from .serializer import register_type


class FormatTask(BaseModel):
    target_blob_url: str | None = None


class FormatTaskResult(BaseModel):
    document: Document
    jurisdiction_id: str
    case_id: str
    redact_error: str | None = None


register_type(FormatTask)
register_type(FormatTaskResult)


@queue.task(
    task_track_started=True,
    task_time_limit=30,
    task_soft_time_limit=25,
    max_retries=3,
    retry_backoff=True,
    autoretry_for=(Exception,),
)
def format(
    redact_result: RedactionTaskResult,
    params: FormatTask,
) -> FormatTaskResult:
    """Format redaction into a Document type."""
    document: Document | None = None

    if not redact_result.error:
        if params.target_blob_url:
            document = Document(
                root=DocumentLink(
                    documentId=redact_result.document_id,
                    attachmentType="LINK",
                    url=params.target_blob_url,
                )
            )
            if not redact_result.content:
                raise ValueError("Missing redacted content")
            write_to_azure_blob_url(params.target_blob_url, redact_result.content)
        else:
            document = format_document(params, redact_result)

    return FormatTaskResult(
        document=document,
        jurisdiction_id=redact_result.jurisdiction_id,
        case_id=redact_result.case_id,
        redact_error=redact_result.error,
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
                url=format_task.target_blob_url,
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
