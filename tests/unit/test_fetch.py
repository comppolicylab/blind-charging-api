import responses

from app.server.generated.models import (
    DocumentContent,
    DocumentLink,
    DocumentText,
    InputDocument,
)
from app.server.tasks import FetchTask, FetchTaskResult, fetch


@responses.activate
def test_fetch_link():
    responses.add(
        responses.GET,
        "http://doc.test.local/abc123",
        body=b"hello world",
        status=200,
    )

    params = FetchTask(
        document=InputDocument(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url="http://doc.test.local/abc123",
            )
        )
    )

    result = fetch.s(params).apply()

    assert result.get() == FetchTaskResult(
        document_id="doc1",
        file_storage_id="b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
    )


@responses.activate
def test_fetch_link_error_code():
    responses.add(
        responses.GET,
        "http://doc.test.local/abc123",
        body=b"failure",
        status=404,
    )

    params = FetchTask(
        document=InputDocument(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url="http://doc.test.local/abc123",
            )
        )
    )

    result = fetch.s(params).apply()

    assert result.get().model_dump() == {
        "document_id": "doc1",
        "file_storage_id": None,
        "errors": [
            {
                "message": "404 Client Error: Not Found for url: http://doc.test.local/abc123",
                "task": "fetch",
                "exception": "HTTPError",
            }
        ],
    }


def test_fetch_text():
    params = FetchTask(
        document=InputDocument(
            root=DocumentText(
                documentId="doc1",
                attachmentType="TEXT",
                content="hello world",
            )
        )
    )

    result = fetch.s(params).apply()

    assert result.get() == FetchTaskResult(
        document_id="doc1",
        file_storage_id="b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
    )


def test_fetch_content():
    params = FetchTask(
        document=InputDocument(
            root=DocumentContent(
                documentId="doc1",
                attachmentType="BASE64",
                content="aGVsbG8gd29ybGQ=",
            )
        )
    )

    result = fetch.s(params).apply()

    assert result.get() == FetchTaskResult(
        document_id="doc1",
        file_storage_id="b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
    )


def test_fetch_content_invalid():
    params = FetchTask(
        document=InputDocument(
            root=DocumentContent(
                documentId="doc1",
                attachmentType="BASE64",
                content="aGVsbG8gd29ybGQ",
            )
        )
    )

    result = fetch.s(params).apply()

    assert result.get().model_dump() == {
        "document_id": "doc1",
        "file_storage_id": None,
        "errors": [
            {
                "message": "Incorrect padding",
                "task": "fetch",
                "exception": "Error",
            }
        ],
    }


def test_fetch_invalid_attachment_type():
    params = FetchTask(
        document=InputDocument(
            root=DocumentContent(
                documentId="doc1",
                attachmentType="BASE64",
                content="aGVsbG8gd29ybGQ",
            )
        )
    )
    params.document.root.attachmentType = "INVALID"

    result = fetch.s(params).apply()

    assert result.get().model_dump() == {
        "document_id": "doc1",
        "file_storage_id": None,
        "errors": [
            {
                "message": "Unsupported attachment type: INVALID",
                "task": "fetch",
                "exception": "ValueError",
            }
        ],
    }
