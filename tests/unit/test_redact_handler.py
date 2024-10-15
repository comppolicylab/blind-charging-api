from typing import cast
from unittest.mock import MagicMock, patch

from fakeredis import FakeRedis
from fastapi.testclient import TestClient
from glowplug import DbDriver
from pydantic import AnyUrl

from app.server.generated.models import Document, DocumentLink, OutputFormat
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
    chain_mock.return_value.apply_async.return_value = "fake_task_id"

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
    assert response.status_code == 200

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=Document(
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
    queue_len = fake_redis_store.llen("jur1:case1:objects")
    assert queue_len == 1
    assert fake_redis_store.lrange("jur1:case1:objects", 0, cast(int, queue_len)) == [
        (
            b'{"callbackUrl": "https://echo/", '
            b'"document": {"attachmentType": "LINK", "documentId": "doc1", '
            b'"url": "https://test_document.pdf/"}, "targetBlobUrl": null}'
        ),
    ]


@patch("app.server.tasks.controller.chain")
async def test_redact_handler_no_callback(
    chain_mock: MagicMock,
    api: TestClient,
    exp_db: DbDriver,
    fake_redis_store: FakeRedis,
):
    # Return a fake task ID when calling `chain().apply_async()`
    chain_mock.return_value.apply_async.return_value = "fake_task_id"

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
    assert response.status_code == 200

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=Document(
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
    queue_len = fake_redis_store.llen("jur1:case1:objects")
    assert queue_len == 1
    assert fake_redis_store.lrange("jur1:case1:objects", 0, cast(int, queue_len)) == [
        (
            b'{"callbackUrl": null, '
            b'"document": {"attachmentType": "LINK", "documentId": "doc1", '
            b'"url": "https://test_document.pdf/"}, "targetBlobUrl": null}'
        ),
    ]

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
    chain_mock.return_value.apply_async.return_value = "fake_task_id"

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
    assert response.status_code == 200

    # Assert that the `chain` function was called with the correct arguments
    chain_mock.assert_called_once()
    # NOTE(jnu): better diff when using `assert` than with `.assert_called_once_with()`
    assert chain_mock.mock_calls[0].args == (
        fetch.s(
            FetchTask(
                document=Document(
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
    queue_len = fake_redis_store.llen("jur1:case1:objects")
    assert queue_len == 2
    assert fake_redis_store.lrange("jur1:case1:objects", 0, cast(int, queue_len)) == [
        (
            b'{"callbackUrl": "https://echo/2", '
            b'"document": {"attachmentType": "LINK", "documentId": "doc2", '
            b'"url": "https://test_document2.pdf/"}, "targetBlobUrl": null}'
        ),
        (
            b'{"callbackUrl": "https://echo/1", '
            b'"document": {"attachmentType": "LINK", "documentId": "doc1", '
            b'"url": "https://test_document.pdf/"}, "targetBlobUrl": null}'
        ),
    ]
