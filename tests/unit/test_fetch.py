import responses

from app.server.generated.models import (
    Document,
    DocumentContent,
    DocumentLink,
    DocumentText,
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
        document=Document(
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
        file_bytes=b"hello world",
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
        document=Document(
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
        "file_bytes": b"",
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
        document=Document(
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
        file_bytes=b"hello world",
    )


def test_fetch_content():
    params = FetchTask(
        document=Document(
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
        file_bytes=b"hello world",
    )


def test_fetch_content_invalid():
    params = FetchTask(
        document=Document(
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
        "file_bytes": b"",
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
        document=Document(
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
        "file_bytes": b"",
        "errors": [
            {
                "message": "Unsupported attachment type: INVALID",
                "task": "fetch",
                "exception": "ValueError",
            }
        ],
    }
