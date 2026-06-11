import base64
from typing import Callable, cast
from urllib.parse import urlparse

import requests
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import AnyUrl, BaseModel

from app.func import allf

from ..case_helper import get_document_sync, save_document_sync, save_retry_state_sync
from ..config import config
from ..generated.models import (
    DocumentContent,
    DocumentLink,
    DocumentText,
    InputDocument,
    UnidentifiedDocumentContent,
    UnidentifiedDocumentLink,
    UnidentifiedInputDocument,
)
from .metrics import (
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, queue
from .serializer import register_type

logger = get_task_logger(__name__)


# --------------------------------------------------------------------------- #
# Document links & fetch-source resolvers
# --------------------------------------------------------------------------- #
#
# Every document is fetched by resolving its LINK url to bytes. The url scheme
# selects a resolver, which lets external sources (https today; s3/azure-blob
# later) plug in uniformly. Inline payloads (BASE64/TEXT) submitted to the API
# are decoded once, persisted to our blob store, and represented as an internal
# ``bcstore://<storage-id>`` link so the rest of the pipeline only ever deals
# with links -- the heavy bytes never transit the Celery broker.
#
# SECURITY: ``bcstore`` is an *internal* scheme. It must never be accepted from
# an inbound request (a client could otherwise read arbitrary blobs by id); the
# API handlers reject any non-http(s) document url before dispatch. Only this
# module mints ``bcstore`` links.

BCSTORE_SCHEME = "bcstore"

# Resolvers turn a link url (as a string) into raw document bytes.
LinkResolver = Callable[[str], bytes]
_LINK_RESOLVERS: dict[str, LinkResolver] = {}


def register_link_resolver(scheme: str, resolver: LinkResolver) -> None:
    """Register a resolver that fetches bytes for links of the given scheme."""
    _LINK_RESOLVERS[scheme.lower()] = resolver


def resolve_link(url: str) -> bytes:
    """Resolve a document link url to raw bytes via the registered resolvers."""
    scheme = urlparse(url).scheme.lower()
    resolver = _LINK_RESOLVERS.get(scheme)
    if resolver is None:
        raise ValueError(f"Unsupported document link scheme: {scheme!r}")
    return resolver(url)


def _resolve_http(url: str) -> bytes:
    response = requests.get(
        url,
        timeout=config.queue.task.link_download_timeout_seconds,
    )
    response.raise_for_status()
    return response.content


def _resolve_bcstore(url: str) -> bytes:
    storage_id = bcstore_storage_id_from_url(url)
    if not storage_id:
        raise ValueError(f"Malformed {BCSTORE_SCHEME} url: {url!r}")
    content = get_document_sync(storage_id)
    if not content:
        raise ValueError(f"No content in store for {url!r}")
    return content


register_link_resolver("http", _resolve_http)
register_link_resolver("https", _resolve_http)
register_link_resolver(BCSTORE_SCHEME, _resolve_bcstore)


def bcstore_url(storage_id: str) -> str:
    """Build an internal ``bcstore://<storage-id>`` link url."""
    return f"{BCSTORE_SCHEME}://{storage_id}"


def bcstore_storage_id_from_url(url: str) -> str | None:
    """Extract the storage id from a ``bcstore://`` url, or ``None``."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != BCSTORE_SCHEME:
        return None
    return parsed.netloc or None


def bcstore_storage_id(
    document: InputDocument | UnidentifiedInputDocument,
) -> str | None:
    """Return the storage id if ``document`` is an internal ``bcstore`` link.

    Used to short-circuit the fetch step: content behind a ``bcstore`` link is
    already staged in our store, so we can pass its id downstream without ever
    loading the bytes into the worker.
    """
    root = document.root
    if root.attachmentType != "LINK":
        return None
    return bcstore_storage_id_from_url(str(root.url))


def make_bcstore_document(document_id: str, storage_id: str) -> InputDocument:
    """Build an identified document that points at staged blob-store content."""
    return InputDocument(
        root=DocumentLink(
            attachmentType="LINK",
            documentId=document_id,
            url=AnyUrl(bcstore_url(storage_id)),
        )
    )


def make_unidentified_bcstore_document(storage_id: str) -> UnidentifiedInputDocument:
    """Build an anonymous document that points at staged blob-store content."""
    return UnidentifiedInputDocument(
        root=UnidentifiedDocumentLink(
            attachmentType="LINK",
            url=AnyUrl(bcstore_url(storage_id)),
        )
    )


class FetchTask(BaseModel):
    # Documents are always represented as links by the time they reach the
    # fetch task. Inline payloads submitted to the API are pre-staged in the
    # blob store and carried as an internal ``bcstore://`` link, so this single
    # field uniformly covers external (https) and internal (bcstore) sources.
    document: InputDocument

    def s(self) -> Signature:
        return fetch.s(self)


class UnidentifiedFetchTask(BaseModel):
    document: UnidentifiedInputDocument
    document_id: str

    def s(self) -> Signature:
        return fetch_unidentified.s(self)


class FetchTaskResult(BaseModel):
    document_id: str
    file_storage_id: str | None = None
    errors: list[ProcessingError] = []


register_type(FetchTask)
register_type(UnidentifiedFetchTask)
register_type(FetchTaskResult)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=config.queue.task.link_download_timeout_seconds + 30,
    task_soft_time_limit=config.queue.task.link_download_timeout_seconds,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def fetch(self, params: FetchTask) -> FetchTaskResult:
    """Fetch the content of a document.

    Args:
        params (FetchTask): The task parameters.

    Returns:
        FetchTaskResult: The task result.
    """
    return _fetch_and_save(self, params.document.root.documentId, params.document)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=config.queue.task.link_download_timeout_seconds + 30,
    task_soft_time_limit=config.queue.task.link_download_timeout_seconds,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def fetch_unidentified(self, params: UnidentifiedFetchTask) -> FetchTaskResult:
    """Fetch the content of an unidentified input document."""
    return _fetch_and_save(self, params.document_id, params.document)


def _fetch_and_save(
    task,
    document_id: str,
    document: InputDocument | UnidentifiedInputDocument,
) -> FetchTaskResult:
    """Fetch document bytes, persist them, and build a task result.

    Shared implementation for the identified and unidentified fetch tasks.
    """
    try:
        # Internal (bcstore) links already point at content staged in our
        # store. Pass the id straight through without loading the bytes into
        # the worker -- this is what keeps large pre-staged payloads off the
        # worker's heap during fetch.
        staged_id = bcstore_storage_id(document)
        if staged_id is not None:
            return FetchTaskResult(document_id=document_id, file_storage_id=staged_id)
        content = fetch_document_content(document)
        return FetchTaskResult(
            document_id=document_id,
            file_storage_id=save_document_sync(content),
        )
    except Exception as e:
        if task.request.retries < task.max_retries:
            logger.warning(f"Fetch task failed: {e}, will be retried.")
            return task.retry(exc=e)
        logger.error(f"Fetch task failed for {document_id}")
        logger.exception(e)
        return FetchTaskResult(
            document_id=document_id,
            errors=[ProcessingError.from_exception("fetch", e)],
        )


def inline_document_bytes(
    document: InputDocument | UnidentifiedInputDocument,
) -> bytes | None:
    """Decode an inline (BASE64/TEXT) document payload to raw bytes.

    Returns ``None`` for non-inline attachment types (e.g. LINK), whose bytes
    are not present in the request and should be fetched by the worker instead.

    This lets the API persist inline payloads to the blob store up front so the
    (potentially large) content never has to be serialized into a Celery
    message and round-tripped through the broker.
    """
    match document.root.attachmentType:
        case "BASE64":
            return base64.b64decode(document.root.content)
        case "TEXT":
            return document.root.content.encode("utf-8")
        case _:
            return None


def fetch_document_content(
    document: InputDocument | UnidentifiedInputDocument,
) -> bytes:
    """Fetch bytes from a supported input document type."""
    match document.root.attachmentType:
        case "LINK":
            if isinstance(document, InputDocument):
                url = cast(DocumentLink, document.root).url
            else:
                url = cast(UnidentifiedDocumentLink, document.root).url
            return resolve_link(str(url))
        case "TEXT":
            if not isinstance(document, InputDocument):
                raise ValueError("TEXT attachment is not supported for anonymous docs.")
            return cast(DocumentText, document.root).content.encode("utf-8")
        case "BASE64":
            if isinstance(document, InputDocument):
                content = cast(DocumentContent, document.root).content
            else:
                content = cast(UnidentifiedDocumentContent, document.root).content
            return base64.b64decode(content)
        case _:
            raise ValueError(
                f"Unsupported attachment type: {document.root.attachmentType}"
            )
