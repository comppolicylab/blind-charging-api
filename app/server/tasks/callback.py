import asyncio
import base64

import requests
from azure.storage.blob import BlobClient
from pydantic import BaseModel

from ..case import get_aliases
from ..config import config
from ..generated.models import (
    Document,
    DocumentContent,
    DocumentLink,
    MaskedSubject,
    RedactionResult,
    RedactionResultSuccess,
)
from .queue import queue
from .redact import RedactionTaskResult
from .serializer import register_type


class CallbackTask(BaseModel):
    callback_url: str | None = None
    target_blob_url: str | None = None


class CallbackTaskResult(BaseModel):
    status_code: int
    response: str | None = None
    # TODO(jnu): we're storing the redaction here redundantly; it would
    # be better to figure out how to retrieve it from the intermediate task results.
    redaction: RedactionTaskResult


register_type(CallbackTask)
register_type(CallbackTaskResult)


_callback_timeout = config.queue.task.callback_timeout_seconds


@queue.task(
    task_track_started=True,
    task_time_limit=_callback_timeout + 10,
    task_soft_time_limit=_callback_timeout,
)
def callback(
    redact_result: RedactionTaskResult, params: CallbackTask
) -> CallbackTaskResult:
    """Post callbacks to the client as requested."""
    document: Document | None = None

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
        document = format_document(redact_result)

    if params.callback_url:
        body = RedactionResult(
            RedactionResultSuccess(
                jurisdictionId=redact_result.jurisdiction_id,
                caseId=redact_result.case_id,
                inputDocumentId=redact_result.document_id,
                maskedSubjects=get_aliases_sync(
                    redact_result.jurisdiction_id, redact_result.case_id
                ),
                redactedDocument=document,
                status="COMPLETE",
            )
        )
        response = requests.post(
            params.callback_url,
            json=body.model_dump(),
        )

        # TODO: figure out retries
        return CallbackTaskResult(
            status_code=response.status_code,
            response=response.text,
            redaction=redact_result,
        )

    return CallbackTaskResult(
        status_code=0, response="[nothing to do]", redaction=redact_result
    )


def get_aliases_sync(jurisdiction_id: str, case_id: str) -> list[MaskedSubject]:
    """Get the masked subjects for a case.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        list[MaskedSubject]: The masked subjects.
    """

    async def _get_aliases_with_store() -> list[MaskedSubject]:
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                return await get_aliases(tx, jurisdiction_id, case_id)

    return asyncio.run(_get_aliases_with_store())


def format_document(redaction: RedactionTaskResult | CallbackTaskResult) -> Document:
    """Format a redacted document for the API response.

    Args:
        redaction (RedactionTaskResult): The redaction results.

    Returns:
        Document: The formatted document.
    """
    if isinstance(redaction, CallbackTaskResult):
        redaction = redaction.redaction
    document_id = redaction.document_id
    if redaction.external_link:
        return Document(
            root=DocumentLink(
                documentId=document_id,
                attachmentType="LINK",
                url=redaction.external_link,
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
