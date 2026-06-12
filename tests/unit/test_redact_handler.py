import base64
import hashlib
from typing import cast
from unittest.mock import MagicMock, patch

from celery.result import AsyncResult
from fakeredis import FakeRedis
from fastapi.testclient import TestClient
from glowplug import DbDriver
from pydantic import AnyUrl

from app.server.generated.models import (
    DocumentLink,
    InputDocument,
    OutputFormat,
)
from app.server.tasks import (
    CallbackTask,
    FetchTask,
    FinalizeTask,
    FormatTask,
    RedactionTask,
    callback,
    fetch,
    finalize,
    format,
    redact,
)


@patch("app.server.tasks.controller.chain")
async def test_redact_handler(
    chain_mock: MagicMock,
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    # Return a fake task ID when calling `chain().apply_async()`
    chain_mock.return_value.apply_async.return_value = AsyncResult("fake_task_id")

    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjects": [
            {
                "role": "accused",
                "subject": {
                    "subjectId": "sub1",
                    "name": "jack doe",
                    "aliases": [
                        {"firstName": "john", "lastName": "p", "middleName": "doe"}
                    ],
                },
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "doc1",
                    "url": "https://test_document.pdf",
                },
                "callbackUrl": "https://echo",
            }
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=InputDocument(
                    root=DocumentLink(
                        attachmentType="LINK",
                        documentId="doc1",
                        url=AnyUrl("https://test_document.pdf"),
                    )
                )
            )
        ),
        redact.s(
            RedactionTask(
                document_id="doc1",
                jurisdiction_id="jur1",
                case_id="case1",
                renderer=OutputFormat.PDF,
            )
        ),
        format.s(FormatTask(target_blob_url=None)),
        callback.s(CallbackTask(callback_url="https://echo/")),
        finalize.s(
            FinalizeTask(
                jurisdiction_id="jur1",
                case_id="case1",
                subject_ids=["sub1"],
                renderer=OutputFormat.PDF,
            )
        ),
    )

    # Check that the right stuff was stored in redis
    assert fake_redis_store.hgetall("jur1:case1:role") == {b"sub1": b"accused"}
    assert fake_redis_store.smembers("jur1:case1:aliases:sub1") == {
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", "nickname": "'
        b'", "suffix": "", "title": ""}',
        b'{"firstName": "john", "lastName": "p", "middleName": "doe", "nickname": '
        b'null, "suffix": null, "title": null}',
    }
    assert fake_redis_store.get("jur1:case1:aliases:sub1:primary") == (
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", '
        b'"nickname": "", "suffix": "", "title": ""}'
    )
    assert fake_redis_store.hgetall("jur1:case1:task") == {b"doc1": b"fake_task_id"}
    # The single object is dispatched immediately, so nothing is left on the
    # work queue.
    assert fake_redis_store.llen("jur1:case1:objects") == 0


@patch("app.server.tasks.controller.chain")
async def test_redact_handler_no_callback(
    chain_mock: MagicMock,
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    # Return a fake task ID when calling `chain().apply_async()`
    chain_mock.return_value.apply_async.return_value = AsyncResult("fake_task_id")

    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjects": [
            {
                "role": "accused",
                "subject": {
                    "subjectId": "sub1",
                    "name": "jack doe",
                    "aliases": [
                        {"firstName": "john", "lastName": "p", "middleName": "doe"}
                    ],
                },
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "doc1",
                    "url": "https://test_document.pdf",
                }
            }
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=InputDocument(
                    root=DocumentLink(
                        attachmentType="LINK",
                        documentId="doc1",
                        url=AnyUrl("https://test_document.pdf"),
                    )
                )
            )
        ),
        redact.s(
            RedactionTask(
                document_id="doc1",
                jurisdiction_id="jur1",
                case_id="case1",
                renderer=OutputFormat.PDF,
            )
        ),
        format.s(FormatTask(target_blob_url=None)),
        callback.s(CallbackTask(callback_url=None)),
        finalize.s(
            FinalizeTask(
                jurisdiction_id="jur1",
                case_id="case1",
                subject_ids=["sub1"],
                renderer=OutputFormat.PDF,
            )
        ),
    )

    # Check that the right stuff was stored in redis
    assert fake_redis_store.hgetall("jur1:case1:role") == {b"sub1": b"accused"}
    assert fake_redis_store.smembers("jur1:case1:aliases:sub1") == {
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", "nickname": "'
        b'", "suffix": "", "title": ""}',
        b'{"firstName": "john", "lastName": "p", "middleName": "doe", "nickname": '
        b'null, "suffix": null, "title": null}',
    }
    assert fake_redis_store.get("jur1:case1:aliases:sub1:primary") == (
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", '
        b'"nickname": "", "suffix": "", "title": ""}'
    )
    assert fake_redis_store.hgetall("jur1:case1:task") == {b"doc1": b"fake_task_id"}
    # The single object is dispatched immediately, so nothing is left on the
    # work queue.
    assert fake_redis_store.llen("jur1:case1:objects") == 0

    # Now check the response from the sync API
    sync_response = api.get("/api/v1/redact/jur1/case1")
    assert sync_response.status_code == 200
    assert sync_response.json() == {
        "caseId": "case1",
        "jurisdictionId": "jur1",
        "requests": [
            {
                "caseId": "case1",
                "inputDocumentId": "doc1",
                "jurisdictionId": "jur1",
                "maskedSubjects": [],
                "status": "QUEUED",
                "statusDetail": (
                    "Redaction request has been received and is queued for processing."
                ),
            },
        ],
    }


@patch("app.server.tasks.controller.chain")
async def test_redact_handler_multi_doc(
    chain_mock: MagicMock,
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    # Return a fake task ID when calling `chain().apply_async()`
    chain_mock.return_value.apply_async.return_value = AsyncResult("fake_task_id")

    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjects": [
            {
                "role": "accused",
                "subject": {
                    "subjectId": "sub1",
                    "name": "jack doe",
                    "aliases": [
                        {"firstName": "john", "lastName": "p", "middleName": "doe"}
                    ],
                },
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "doc1",
                    "url": "https://test_document.pdf",
                },
                "callbackUrl": "https://echo/1",
            },
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "doc2",
                    "url": "https://test_document2.pdf",
                },
                "callbackUrl": "https://echo/2",
            },
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=InputDocument(
                    root=DocumentLink(
                        attachmentType="LINK",
                        documentId="doc1",
                        url=AnyUrl("https://test_document.pdf"),
                    )
                )
            )
        ),
        redact.s(
            RedactionTask(
                document_id="doc1",
                jurisdiction_id="jur1",
                case_id="case1",
                renderer=OutputFormat.PDF,
            )
        ),
        format.s(FormatTask(target_blob_url=None)),
        callback.s(CallbackTask(callback_url="https://echo/1")),
        finalize.s(
            FinalizeTask(
                jurisdiction_id="jur1",
                case_id="case1",
                subject_ids=["sub1"],
                renderer=OutputFormat.PDF,
            )
        ),
    )

    # Check that the right stuff was stored in redis
    assert fake_redis_store.hgetall("jur1:case1:role") == {b"sub1": b"accused"}
    assert fake_redis_store.smembers("jur1:case1:aliases:sub1") == {
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", "nickname": "'
        b'", "suffix": "", "title": ""}',
        b'{"firstName": "john", "lastName": "p", "middleName": "doe", "nickname": '
        b'null, "suffix": null, "title": null}',
    }
    assert fake_redis_store.get("jur1:case1:aliases:sub1:primary") == (
        b'{"firstName": "jack", "lastName": "doe", "middleName": "", '
        b'"nickname": "", "suffix": "", "title": ""}'
    )
    assert fake_redis_store.hgetall("jur1:case1:task") == {b"doc1": b"fake_task_id"}
    # Only the *non-dispatched* objects are enqueued for later processing. The
    # first object (doc1) is dispatched immediately and is not placed on the
    # queue, so the queue contains just doc2.
    queue_len = fake_redis_store.llen("jur1:case1:objects")
    assert queue_len == 1
    assert fake_redis_store.lrange("jur1:case1:objects", 0, cast(int, queue_len)) == [
        (
            b'{"callbackUrl": "https://echo/2", '
            b'"document": {"attachmentType": "LINK", "documentId": "doc2", '
            b'"url": "https://test_document2.pdf/"}, "targetBlobUrl": null}'
        ),
    ]


@patch("app.server.tasks.controller.chain")
async def test_redact_handler_base64_payload_skips_broker(
    chain_mock: MagicMock,
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    """Inline BASE64 content is persisted to the blob store before dispatch.

    Rather than carrying the (potentially large) document inline through the
    Celery broker, the handler decodes it once, writes it to the blob store,
    and the fetch task references it via an internal ``bcstore://`` link. This
    is the headroom fix for the BASE64 attachment path.
    """
    chain_mock.return_value.apply_async.return_value = AsyncResult("fake_task_id")

    raw = b"%PDF-1.4 fake document bytes"
    storage_id = hashlib.sha256(raw).hexdigest()

    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjects": [
            {
                "role": "accused",
                "subject": {"subjectId": "sub1", "name": "jack doe"},
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "BASE64",
                    "documentId": "doc1",
                    "content": base64.b64encode(raw).decode("ascii"),
                },
                "callbackUrl": "https://echo",
            }
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # The fetch task should carry an internal bcstore link, not the payload.
    chain_mock.assert_called_once()
    fetch_sig = chain_mock.mock_calls[0].args[0]
    assert fetch_sig == fetch.s(
        FetchTask(
            document=InputDocument(
                root=DocumentLink(
                    attachmentType="LINK",
                    documentId="doc1",
                    url=AnyUrl(f"bcstore://{storage_id}"),
                )
            )
        )
    )

    # The decoded bytes (not the base64 string) live in the blob store.
    assert fake_redis_store.get(storage_id) == raw


async def test_redact_handler_rejects_internal_document_scheme(
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    """A client must not be able to submit an internal ``bcstore://`` link."""
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjects": [
            {
                "role": "accused",
                "subject": {"subjectId": "sub1", "name": "jack doe"},
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "doc1",
                    "url": "bcstore://deadbeef",
                },
                "callbackUrl": "https://echo",
            }
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 400
