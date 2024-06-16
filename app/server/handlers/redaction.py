import asyncio
import base64
import logging
from typing import List

import aiohttp
from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from starlette.background import BackgroundTask

from ..config import config
from ..db import File, JobStatus, Redaction, Task
from ..generated.models import (
    Document,
    DocumentContent,
    DocumentLink,
    RedactionRequest,
    RedactionResult,
    RedactionResultError,
    RedactionResultPending,
    RedactionResultSuccess,
    RedactionStatus,
)
from ..time import expire_h

logger = logging.getLogger(__name__)


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
    new_tasks = 0
    for new_objects in results:
        for object in new_objects:
            if isinstance(object, Task):
                new_tasks += 1
            request.state.db.add(object)

    logger.debug(f"Created {new_tasks} redaction task(s).")
    response = Response(status_code=202)
    if new_tasks:
        logger.debug("Scheduling a background task to check for work.")
        # Schedule this in the background because we need to commit the session
        # before the work will be available in the queue. The background task
        # will execute after the request is complete / the response is sent.
        response.background = BackgroundTask(request.state.redaction_processor.check)
    return response


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
        external_id=doc.root.documentId,
        jurisdiction_id=jurisdiction_id,
        case_id=case_id,
        content=content,
    )

    # Create a placeholder redaction
    redaction = Redaction(file=file)

    # Create a task to process this document
    task = Task(
        redaction=redaction,
        callback_url=str(callback_url),
        expires_at=expire_h(hours=config.task.retention_hours),
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
    match doc.root.attachmentType:
        case "LINK":
            async with aiohttp.ClientSession() as session:
                async with session.get(str(doc.root.url)) as response:
                    content = await response.read()
        case "TEXT":
            content = doc.root.content.encode("utf-8")
        case "BASE64":
            content = base64.b64decode(doc.root.content)
        case _:
            raise ValueError(f"Unsupported attachment type: {doc.root.attachmentType}")
    return content


async def get_redaction_status(
    *,
    request: Request,
    jurisdiction_id: str,
    case_id: str,
    accused_id: str | None = None,
) -> RedactionStatus:
    """Get the redaction status for a case.

    Args:
        request (Request): The incoming request.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        accused_id (str, optional): The accused ID.

    Returns:
        RedactionStatus: The redaction status summary.
    """
    redaction_results = list[RedactionResult]()

    # Get the redaction status from the database
    files_q = select(File).filter(
        File.jurisdiction_id == jurisdiction_id, File.case_id == case_id
    )
    result = await request.state.db.execute(files_q)
    for file in result.scalars().all():
        latest_redaction = await file.latest_redaction(request.state.db)
        if not latest_redaction:
            raise ValueError(f"No redaction found for file {file.external_id}")
        latest_job = await latest_redaction.task.latest_job(request.state.db)

        # The job should exist, but if it doesn't, we'll just assume it's queued.
        if not latest_job:
            redaction_results.append(
                RedactionResult(
                    RedactionResultPending(
                        jurisdictionId=jurisdiction_id,
                        caseId=case_id,
                        maskedAccuseds=[],  # TODO
                        status="QUEUED",
                    )
                )
            )
            continue

        match latest_job.status:
            case JobStatus.queued:
                redaction_results.append(
                    RedactionResult(
                        RedactionResultPending(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            maskedAccuseds=[],  # TODO
                            status="QUEUED",
                        )
                    )
                )
            case JobStatus.processing:
                redaction_results.append(
                    RedactionResult(
                        RedactionResultPending(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            maskedAccuseds=[],  # TODO
                            status="PROCESSING",
                        )
                    )
                )
            case JobStatus.success:
                redaction_results.append(
                    RedactionResult(
                        RedactionResultSuccess(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            maskedAccuseds=[],  # TODO
                            status="COMPLETE",
                            redactedDocument=format_document(latest_redaction),
                        )
                    )
                )
            case JobStatus.error:
                redaction_results.append(
                    RedactionResult(
                        RedactionResultError(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            maskedAccuseds=[],  # TODO
                            status="ERROR",
                            error=latest_job.error,
                        )
                    )
                )
            case _:
                raise ValueError(f"Unknown job status: {latest_job.status}")

    return RedactionStatus(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        requests=redaction_results,
    )


def format_document(redaction: Redaction) -> Document:
    """Format a redacted document for the API response.

    Args:
        redaction (Redaction): The redaction object.

    Returns:
        Document: The formatted document.
    """
    document_id = redaction.file.external_id
    if redaction.external_link:
        return Document(
            root=DocumentLink(
                documentId=document_id,
                attachmentType="LINK",
                url=redaction.external_link,
            )
        )
    else:
        return Document(
            root=DocumentContent(
                documentId=document_id,
                attachmentType="BASE64",
                content=base64.b64encode(redaction.content).decode("utf-8"),
            )
        )
