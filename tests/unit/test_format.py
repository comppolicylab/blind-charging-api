from unittest.mock import patch

from fakeredis import FakeRedis
from pydantic import AnyUrl

from app.server.generated.models import Document, DocumentContent, DocumentLink
from app.server.tasks import (
    FormatTask,
    FormatTaskResult,
    ProcessingError,
    RedactionTaskResult,
    format,
)


def test_format_no_blob(fake_redis_store: FakeRedis):
    fake_redis_store.set("abc123", b"content")
    redact_result = RedactionTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        file_storage_id="abc123",
        errors=[],
    )

    result = format.s(redact_result, FormatTask()).apply()
    assert result.get() == FormatTaskResult(
        document=Document(
            root=DocumentContent(
                documentId="doc1",
                attachmentType="BASE64",
                content="Y29udGVudA==",
            )
        ),
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        errors=[],
    )


@patch("azure.storage.blob.BlobClient.upload_blob")
def test_format_with_blob_url(blob_upload, fake_redis_store: FakeRedis):
    fake_redis_store.set("abc123", b"content")
    redact_result = RedactionTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        file_storage_id="abc123",
        errors=[],
    )

    # Some fake SAS URL
    blob_sas_url = "https://teststorage.blob.core.windows.net/testcontainer/testblob.pdf?sv=2019-12-12&st=2021-10-01T00%3A00%3A00Z&se=2021-10-01T00%3A00%3A00Z&sr=b&sp=r&sig=abc123"

    result = format.s(redact_result, FormatTask(target_blob_url=blob_sas_url)).apply()
    assert result.get() == FormatTaskResult(
        document=Document(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url=AnyUrl(blob_sas_url),
            )
        ),
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        errors=[],
    )
    blob_upload.assert_called_once()


@patch("azure.storage.blob.BlobClient.upload_blob")
def test_format_with_invalid_blob_url(blob_upload, fake_redis_store: FakeRedis):
    fake_redis_store.set("abc123", b"content")
    redact_result = RedactionTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        file_storage_id="abc123",
        errors=[],
    )

    result = format.s(
        redact_result, FormatTask(target_blob_url="http://azure.blob.local/abc123")
    ).apply()

    error_msg = "Invalid URL. Provide a blob_url with a valid blob and container name."
    assert (
        result.get().model_dump()
        == FormatTaskResult(
            document=None,
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[
                ProcessingError(
                    message=error_msg,
                    task="format",
                    exception="ValueError",
                )
            ],
        ).model_dump()
    )
    blob_upload.assert_not_called()


def test_format_errors():
    redact_result = RedactionTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        file_storage_id=None,
        errors=[
            ProcessingError(
                message="error",
                task="redact",
                exception="Exception",
            )
        ],
    )

    result = format.s(redact_result, FormatTask()).apply()
    assert result.get() == FormatTaskResult(
        document=None,
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        errors=[
            ProcessingError(
                message="error",
                task="redact",
                exception="Exception",
            )
        ],
    )
