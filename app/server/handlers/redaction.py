import asyncio
import logging
from urllib.parse import urlparse

from celery import chain
from fastapi import HTTPException, Request
from nameparser import HumanName

from ..case import CaseStore
from ..config import config
from ..generated.models import (
    HumanName as HumanNameModel,
)
from ..generated.models import (
    RedactionRequest,
    RedactionResult,
    RedactionResultError,
    RedactionResultPending,
    RedactionResultSuccess,
    RedactionStatus,
    RedactionTarget,
)
from ..generated.models import (
    Subject as SubjectModel,
)
from ..tasks import (
    CallbackTask,
    FetchTask,
    RedactionTask,
    callback,
    fetch,
    get_result,
    redact,
)
from ..tasks.callback import format_document

logger = logging.getLogger(__name__)


# Only allow HTTP callbacks in debug mode
_callback_schemes = {"http", "https"} if config.debug else {"https"}
_disallowed_callback_hosts = {"localhost", "127.0.0.1"} if not config.debug else set()


def validate_callback_url(url: str | None) -> None:
    """Validate a callback URL.

    Args:
        url (str): The URL to validate.

    Raises:
        HTTPException: If the URL is invalid.
    """
    if not url:
        return
    parsed = urlparse(url)
    if not parsed.scheme:
        raise HTTPException(status_code=400, detail="Invalid callback URL")
    if parsed.scheme not in _callback_schemes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid callback URL scheme (must use {_callback_schemes})",
        )
    # Prevent use of localhost (etc) in production
    if parsed.hostname in _disallowed_callback_hosts:
        raise HTTPException(status_code=400, detail="Invalid callback URL host")


async def redact_documents(*, request: Request, body: RedactionRequest) -> None:
    """Handle requests to redact a document.

    Args:
        request (Request): The incoming request.
        body (RedactionRequest): The request body.

    Raises:
        HTTPException: If the document content cannot be fetched.
    """
    store = CaseStore(request.state.store)
    await store.init(body.jurisdictionId, body.caseId)

    logger.debug(f"Received redaction request for {body.jurisdictionId}-{body.caseId}")

    subject_ids = set[str]()
    subject_role_mapping = dict[str, str]()
    # Process the individuals submitted with the request
    for subj in body.subjects:
        subject_role_mapping[subj.subject.subjectId] = subj.role
        for i, alias in enumerate(process_subject(subj)):
            primary = i == 0
            await store.save_alias(subj.subject.subjectId, alias, primary=primary)
        subject_ids.add(subj.subject.subjectId)
    await store.save_roles(subject_role_mapping)

    subj_ids_list = list(subject_ids)

    # Create a task for each document
    for obj in body.objects:
        task_chain = create_document_redaction_task(
            body.jurisdictionId, body.caseId, subj_ids_list, obj
        )
        # Start the task chain
        task_id = task_chain.apply_async()
        doc_id = obj.document.root.documentId
        logger.debug(f"Created redaction task {task_id} for document {doc_id}.")
        # Save the task to the database
        await store.save_doc_task(doc_id, str(task_id))


def process_subject(subject: SubjectModel) -> list[HumanNameModel]:
    """Process a subject for the database

    Args:
        subjects (list[SubjectModel]): The subjects to process.

    Returns:
        HumanNameModel: The processed subject.
    """
    aliases: list[HumanNameModel] = []
    all_aliases = [subject.subject.name] + (subject.subject.aliases or [])
    for alias in all_aliases:
        if isinstance(alias, str):
            parsed_name = HumanName(alias)
            aliases.append(
                HumanNameModel(
                    firstName=parsed_name.first,
                    middleName=parsed_name.middle,
                    lastName=parsed_name.last,
                    suffix=parsed_name.suffix,
                    title=parsed_name.title,
                    nickname=parsed_name.nickname,
                )
            )
        else:
            aliases.append(alias)

    logger.debug(f"Processed {len(aliases)} aliases for {subject.subject.subjectId}.")

    return aliases


def create_document_redaction_task(
    jurisdiction_id: str, case_id: str, subject_ids: list[str], object: RedactionTarget
) -> chain:
    """Create database objects representing a document redaction task.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        subject_ids (list[str]): The IDs of the subjects to redact.
        object (RedactionTarget): The document to redact.

    Returns:
        chain: The Celery chain representing the redaction pipeline.
    """
    callback_url = str(object.callbackUrl) if object.callbackUrl else None
    target_blob_url = str(object.targetBlobUrl) if object.targetBlobUrl else None
    validate_callback_url(callback_url)
    validate_callback_url(target_blob_url)

    fd_task_params = FetchTask(
        document=object.document,
    )

    r_task_params = RedactionTask(
        document_id=object.document.root.documentId,
        jurisdiction_id=jurisdiction_id,
        case_id=case_id,
        subject_ids=subject_ids,
    )

    cb_task_params = CallbackTask(
        callback_url=callback_url,
        target_blob_url=target_blob_url,
    )

    # TODO: error handling, iterative processing
    return chain(
        fetch.s(fd_task_params),
        redact.s(r_task_params),
        callback.s(cb_task_params),
    )


async def get_redaction_status(
    *,
    request: Request,
    jurisdiction_id: str,
    case_id: str,
    subject_id: str | None = None,
) -> RedactionStatus:
    """Get the redaction status for a case.

    Args:
        request (Request): The incoming request.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        subject_id (str, optional): The ID of a person to get redaction status for.

    Returns:
        RedactionStatus: The redaction status summary.
    """
    store = CaseStore(request.state.store)
    await store.init(jurisdiction_id, case_id)

    redaction_results = list[RedactionResult]()

    if subject_id:
        raise NotImplementedError("Filtering by subject ID is not yet implemented.")

    # Get the redaction status from the database
    tasks, masked_subjects = await asyncio.gather(
        store.get_doc_tasks(),
        store.get_aliases(),
    )

    for doc_id, task_id in tasks.items():
        # Get the status of the task from celery
        task_result = get_result(task_id)

        # The job should exist, but if it doesn't, we'll just assume it's queued.
        if not task_result:
            redaction_results.append(
                RedactionResult(
                    RedactionResultPending(
                        jurisdictionId=jurisdiction_id,
                        caseId=case_id,
                        inputDocumentId=doc_id,
                        maskedSubjects=masked_subjects,
                        status="QUEUED",
                    )
                )
            )
            continue

        match task_result.status:
            case "PENDING" | "RETRY":
                redaction_results.append(
                    RedactionResult(
                        RedactionResultPending(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            inputDocumentId=doc_id,
                            maskedSubjects=masked_subjects,
                            status="QUEUED",
                        )
                    )
                )
            case "STARTED":
                redaction_results.append(
                    RedactionResult(
                        RedactionResultPending(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            inputDocumentId=doc_id,
                            maskedSubjects=masked_subjects,
                            status="PROCESSING",
                        )
                    )
                )
            case "SUCCESS":
                redaction_results.append(
                    RedactionResult(
                        RedactionResultSuccess(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            inputDocumentId=doc_id,
                            maskedSubjects=masked_subjects,
                            status="COMPLETE",
                            redactedDocument=format_document(task_result.result),
                        )
                    )
                )
            case "FAILURE":
                redaction_results.append(
                    RedactionResult(
                        RedactionResultError(
                            jurisdictionId=jurisdiction_id,
                            caseId=case_id,
                            inputDocumentId=doc_id,
                            maskedSubjects=masked_subjects,
                            status="ERROR",
                            error=str(task_result.result),
                        )
                    )
                )
            case _:
                raise ValueError(f"Unknown job status: {task_result.status}")

    return RedactionStatus(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        requests=redaction_results,
    )
