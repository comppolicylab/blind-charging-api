import base64

import requests
from pydantic import BaseModel

from ..generated.models import Document, DocumentContent, DocumentLink
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


@queue.task(task_track_started=True, task_time_limit=300, task_soft_time_limit=240)
def callback(
    redact_result: RedactionTaskResult, params: CallbackTask
) -> CallbackTaskResult:
    """Post callbacks to the client as requested."""
    if params.target_blob_url:
        raise NotImplementedError("Blob storage not implemented")

    if params.callback_url:
        response = requests.post(
            params.callback_url,
            # TODO redact_result might could be formatted into the link
            json=format_document(redact_result).model_dump(),
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
