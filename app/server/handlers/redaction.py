import asyncio
import base64
from typing import List

import aiohttp
from fastapi import HTTPException, Request

from ..config import config
from ..db import File, Task
from ..generated.models import Document, RedactionRequest, RedactionStatus
from ..time import expire_h


async def redact_documents(*, request: Request, body: RedactionRequest) -> None:
    """Handle requests to redact a document.

    Args:
        request (Request): The incoming request.
        body (RedactionRequest): The request body.

    Raises:
        HTTPException: If the document content cannot be fetched.
    """
    # Create a task for each document. Do this concurrently since the files will
    # often be on remote servers that we can fetch simultaneously.
    results = await asyncio.gather(
        *[
            create_document_redaction_task(
                body.jurisdictionId, body.caseId, doc, body.callbackUrl
            )
            for doc in body.documents
        ]
    )

    # Save the tasks to the database
    for new_objects in results:
        for object in new_objects:
            request.state.db.add(object)


async def create_document_redaction_task(
    jurisdiction_id: str, case_id: str, doc: Document, callback_url: str | None = None
) -> List[File | Task]:
    """Create database objects representing a document redaction task.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        doc (Document): The document to redact.
        callback_url (str, optional): The URL to call when the task is complete.

    Returns:
        list[File, Task]: The file and task objects.
    """
    try:
        content = await fetch_document_content(doc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Insert document into database
    file = File(
        external_id=doc.documentId,
        jurisdiction_id=jurisdiction_id,
        case_id=case_id,
        content=content,
    )

    # Create a task to process this document
    task = Task(
        file=file,
        callback_url=callback_url,
        expires_at=expire_h(hours=config.retention.hours),
    )

    return [file, task]


async def fetch_document_content(doc: Document) -> bytes:
    """Fetch the content of a document.

    Args:
        doc (Document): The document to fetch.

    Returns:
        bytes: The content of the document.
    """
    content = b""
    match doc.attachmentType:
        case "LINK":
            async with aiohttp.ClientSession() as session:
                async with session.get(doc.link) as response:
                    content = await response.read()
        case "TEXT":
            content = doc.content.encode("utf-8")
        case "BASE64":
            content = base64.b64decode(doc.content)
        case _:
            raise ValueError(f"Unsupported attachment type: {doc.attachmentType}")
    return content


async def get_redaction_status(
    *, request: Request, jurisdiction_id: str, case_id: str
) -> RedactionStatus:
    return RedactionStatus(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        requests=[],
    )
