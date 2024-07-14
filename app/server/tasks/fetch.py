import base64

import requests
from pydantic import BaseModel

from ..config import config
from ..generated.models import Document
from .queue import queue
from .serializer import register_type


class FetchTask(BaseModel):
    document: Document


class FetchTaskResult(BaseModel):
    document_id: str
    file_bytes: bytes


register_type(FetchTask)
register_type(FetchTaskResult)


@queue.task(
    task_track_started=True,
    task_time_limit=config.queue.task.link_download_timeout_seconds + 30,
    task_soft_time_limit=config.queue.task.link_download_timeout_seconds,
)
def fetch(params: FetchTask) -> FetchTaskResult:
    """Fetch the content of a document.

    Args:
        params (FetchTask): The task parameters.

    Returns:
        FetchTaskResult: The task result.
    """
    content = b""
    match params.document.root.attachmentType:
        case "LINK":
            response = requests.get(
                params.document.root.url,
                timeout=config.queue.task.link_download_timeout_seconds,
            )
            response.raise_for_status()
            content = response.content
        case "TEXT":
            content = params.document.root.content.encode("utf-8")
        case "BASE64":
            content = base64.b64decode(params.document.root.content)
        case _:
            raise ValueError(
                f"Unsupported attachment type: {params.document.root.attachmentType}"
            )

    return FetchTaskResult(
        document_id=params.document.root.documentId, file_bytes=content
    )
