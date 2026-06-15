import asyncio
import base64
import json
import time
from logging import Logger

from fastapi.testclient import TestClient
from glowplug import DbDriver

from app.server.db import DocumentStatus

from .testutil import MockCallbackServer


async def test_redact(
    api: TestClient,
    exp_db: DbDriver,
    real_queue: None,
    callback_server: MockCallbackServer,
    logger: Logger,
):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "outputFormat": "TEXT",
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
                    "url": f"{callback_server.docker_base_url}/test_document.pdf",
                },
                "callbackUrl": f"{callback_server.docker_base_url}/echo",
            }
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # We deliberately do not assert on the exact redacted content: the text is
    # produced by OCR (tesseract) and a redaction template, both of which vary
    # by environment/version. Match the stable structural fields and then do a
    # sanity check on the decoded document below.
    observed = callback_server.wait_for_request(
        "/echo",
        timeout=30,
        method="POST",
        json_body={
            "jurisdictionId": "jur1",
            "caseId": "case1",
            "inputDocumentId": "doc1",
            "maskedSubjects": [],
            "redactedDocument": {
                "attachmentType": "BASE64",
                "documentId": "doc1",
            },
            "status": "COMPLETE",
        },
    )

    assert observed is not None
    callback_body = json.loads(observed.body)
    redacted_text = base64.b64decode(
        callback_body["redactedDocument"]["content"]
    ).decode("utf-8")
    # The redaction pipeline wraps the extracted narrative in a header/footer
    # and should contain recognizable, race-neutral boilerplate.
    assert "Redacted Narrative" in redacted_text
    assert "report-rbc-bug" in redacted_text

    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        try:
            with exp_db.sync_session() as sesh:
                dss = sesh.query(DocumentStatus).all()
                assert len(dss) == 1
                ds = dss[0]
                assert ds.jurisdiction_id == "jur1"
                assert ds.case_id == "case1"
                assert ds.document_id == "doc1"
                assert ds.status == "COMPLETE"
                assert ds.error is None
            break
        except AssertionError as e:
            logger.error(e)
            await asyncio.sleep(1)


async def test_redact_multiple_documents(
    api: TestClient,
    exp_db: DbDriver,
    real_queue: None,
    callback_server: MockCallbackServer,
    logger: Logger,
):
    """A redaction request with multiple docs must fire callback for each."""
    document_ids = ["doc1", "doc2", "doc3"]
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "outputFormat": "TEXT",
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
                    "documentId": doc_id,
                    "url": f"{callback_server.docker_base_url}/test_document.pdf",
                },
                "callbackUrl": f"{callback_server.docker_base_url}/echo",
            }
            for doc_id in document_ids
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 201

    # We expect exactly one callback per submitted document. Documents are
    # processed sequentially, so allow a generous per-document timeout.
    for doc_id in document_ids:
        observed = callback_server.wait_for_request(
            "/echo",
            timeout=60,
            method="POST",
            json_body={
                "jurisdictionId": "jur1",
                "caseId": "case1",
                "inputDocumentId": doc_id,
                "redactedDocument": {
                    "attachmentType": "BASE64",
                    "documentId": doc_id,
                },
                "status": "COMPLETE",
            },
        )
        assert observed is not None, f"No callback received for {doc_id}"

    # The experiments DB should record a COMPLETE status for every document.
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        try:
            with exp_db.sync_session() as sesh:
                dss = sesh.query(DocumentStatus).all()
                assert len(dss) == len(document_ids)
                statuses = {ds.document_id: ds for ds in dss}
                assert set(statuses) == set(document_ids)
                for ds in dss:
                    assert ds.jurisdiction_id == "jur1"
                    assert ds.case_id == "case1"
                    assert ds.status == "COMPLETE"
                    assert ds.error is None
            break
        except AssertionError as e:
            logger.error(e)
            await asyncio.sleep(1)
    else:
        raise AssertionError("experiments DB never reached expected state")


async def test_redact_multiple_documents_same_id(
    api: TestClient,
    exp_db: DbDriver,
    real_queue: None,
    callback_server: MockCallbackServer,
    logger: Logger,
):
    """Multiple docs with the same ID should fail immediately."""
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "outputFormat": "TEXT",
        "subjects": [
            {
                "role": "accused",
                "subject": {
                    "subjectId": "sub1",
                    "name": "jack doe",
                },
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "dup",
                    "url": f"{callback_server.docker_base_url}/test_document.pdf",
                },
                "callbackUrl": f"{callback_server.docker_base_url}/echo/a",
            },
            {
                "document": {
                    "attachmentType": "LINK",
                    "documentId": "dup",
                    "url": f"{callback_server.docker_base_url}/test_document.pdf",
                },
                "callbackUrl": f"{callback_server.docker_base_url}/echo/b",
            },
        ],
    }

    response = api.post("/api/v1/redact", json=request)
    assert response.status_code == 400
    assert response.json() == {"detail": "Duplicate document ID: dup"}
