import asyncio
import logging
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from nameparser import HumanName

from ..case import CaseStore
from ..config import config
from ..generated.models import (
    HumanName as HumanNameModel,
)
from ..generated.models import (
    OutputFormat,
    RedactionRequest,
    RedactionResult,
    RedactionResultError,
    RedactionResultPending,
    RedactionResultSuccess,
    RedactionStatus,
)
from ..generated.models import (
    Subject as SubjectModel,
)
from ..tasks import create_document_redaction_task, get_result

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


def validate_redaction_request(body: RedactionRequest) -> None:
    """Validate a redaction request.

    Args:
        body (RedactionRequest): The request body.

    Raises:
        HTTPException: If the request is invalid.
    """
    if not body.objects:
        raise HTTPException(status_code=400, detail="No objects to redact")
    for obj in body.objects:
        callback_url = str(obj.callbackUrl) if obj.callbackUrl else None
        target_blob_url = str(obj.targetBlobUrl) if obj.targetBlobUrl else None
        validate_callback_url(callback_url)
        # TODO - the SAS url validation should be more involved than just "valid URL."
        # We can try to use the Azure SDK to validate the URL.
        validate_callback_url(target_blob_url)


async def redact_documents(*, request: Request, body: RedactionRequest) -> None:
    """Handle requests to redact a document.

    Args:
        request (Request): The incoming request.
        body (RedactionRequest): The request body.

    Raises:
        HTTPException: If the document content cannot be fetched.
    """
    # Do some extra validation that Pydantic doesn't currently catch
    validate_redaction_request(body)

    store = CaseStore(request.state.store)
    await store.init(body.jurisdictionId, body.caseId)

    logger.debug(f"Received redaction request for {body.jurisdictionId}-{body.caseId}")

    subject_ids = set[str]()
    subject_role_mapping = dict[str, str]()
    # Process the individuals submitted with the request
    for subj in body.subjects:
        subject_role_mapping[subj.subject.subjectId] = subj.role
        for i, name_variant in enumerate(process_subject(subj)):
            primary = i == 0
            await store.save_real_name(
                subj.subject.subjectId, name_variant, primary=primary
            )
        subject_ids.add(subj.subject.subjectId)
    await store.save_roles(subject_role_mapping)

    subj_ids_list = list(subject_ids)

    # TODO(jnu): make sure that document is not currently being redacted!
    # Basically, it doesn't matter if the document is already redacted, but
    # we should not start a new redaction task if the document is already being
    # redacted. This is because we want to iteratively build information about
    # the case from each document. If we start a new task while a chain is in
    # progress, we can't use that context.
    # TODO(jnu): decide if we should just reject the request, or enqueue it.

    # Create a task chain to process the documents. The chain will
    # iteratively create new chains for each document in the request.
    task_chain = create_document_redaction_task(
        body.jurisdictionId,
        body.caseId,
        subj_ids_list,
        body.objects[0],
        renderer=body.outputFormat or OutputFormat.PDF,
    )

    if not task_chain:
        # Unclear why we would ever get here, but throw an error just in case.
        raise HTTPException(status_code=500, detail="Failed to create redaction task")

    # Save the list of objects that need to be redacted for future reference.
    await store.save_objects_list(body.objects)

    # Start the task chain
    task_id = task_chain.apply_async()
    doc_id = body.objects[0].document.root.documentId
    logger.debug(f"Created redaction task {task_id} for document {doc_id}.")
    # Save the new task to the database
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
        store.get_masked_names(),
    )

    for doc_id, task_id in tasks.items():
        # Get the status of the task from celery
        task_result = get_result(task_id)

        # The job should exist, but if it doesn't, we'll just assume it's queued.
        # TODO(jnu): do some remediation here to ensure that job is actually started
        if not task_result:
            redaction_results.append(
                RedactionResult(
                    RedactionResultPending(
                        jurisdictionId=jurisdiction_id,
                        caseId=case_id,
                        inputDocumentId=doc_id,
                        maskedSubjects=masked_subjects,
                        status="QUEUED",
                        statusDetail="Task not found",
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
                            statusDetail="Task pending"
                            if task_result.status == "PENDING"
                            else "Task retrying",
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
                            statusDetail="Task is currently being processed",
                        )
                    )
                )
            case "SUCCESS":
                doc = await store.get_result_doc(doc_id)
                if not doc:
                    redaction_results.append(
                        RedactionResult(
                            RedactionResultError(
                                jurisdictionId=jurisdiction_id,
                                caseId=case_id,
                                inputDocumentId=doc_id,
                                maskedSubjects=masked_subjects,
                                status="ERROR",
                                error="Redaction job completed, but result is missing",
                            )
                        )
                    )
                else:
                    redaction_results.append(
                        RedactionResult(
                            RedactionResultSuccess(
                                jurisdictionId=jurisdiction_id,
                                caseId=case_id,
                                inputDocumentId=doc_id,
                                maskedSubjects=masked_subjects,
                                status="COMPLETE",
                                redactedDocument=doc,
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
