import pathlib
import re
from typing import cast

from bc2.core.inspect.quality import QualityReport
from fakeredis import FakeRedis

from app.server.generated.models import OutputFormat
from app.server.tasks import (
    FetchTaskResult,
    ProcessingError,
    RedactionTask,
    RedactionTaskResult,
    redact,
)
from app.server.tasks.redact import check_quality

this_dir = pathlib.Path(__file__).parent
sample_data_dir = this_dir.parent.parent / "app" / "server" / "sample_data"
sample_pdf = sample_data_dir / "simple.pdf"
sample_ocr = sample_data_dir / "simple.ocr.txt"


def test_redact(fake_redis_store: FakeRedis):
    fake_redis_store.set("abc123", sample_pdf.read_bytes())
    fetch_result = FetchTaskResult(
        document_id="doc1",
        file_storage_id="abc123",
    )

    redact_task = RedactionTask(
        document_id="doc1",
        jurisdiction_id="jur1",
        case_id="case1",
        renderer=OutputFormat.TEXT,
    )

    result = redact.s(fetch_result, redact_task).apply().get()

    # Tesseract can give different results on different systems, so we can't
    # compare the exact content. Usually it's just the whitespace that differs,
    # so compare with whitespace stripped.
    content = cast(bytes, fake_redis_store.get(result.file_storage_id))
    assert re.sub(r"\s+", "", content.decode("utf-8")) == re.sub(
        r"\s+", "", sample_ocr.read_text()
    )
    assert result.model_dump(exclude=["file_storage_id"]) == {
        "jurisdiction_id": "jur1",
        "case_id": "case1",
        "document_id": "doc1",
        "errors": [],
        "renderer": OutputFormat.TEXT,
    }


def test_redact_errors():
    fetch_result = FetchTaskResult(
        document_id="doc1",
        file_bytes=b"",
        errors=[
            ProcessingError(
                message="error",
                task="fetch",
                exception="Exception",
            )
        ],
    )

    redact_task = RedactionTask(
        document_id="doc1",
        jurisdiction_id="jur1",
        case_id="case1",
    )

    result = redact.s(fetch_result, redact_task).apply()
    assert result.get() == RedactionTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        errors=fetch_result.errors,
        renderer=OutputFormat.PDF,
    )


def test_redact_new_errors(fake_redis_store: FakeRedis):
    fake_redis_store.set("abc123", b"unreadable")
    fetch_result = FetchTaskResult(
        document_id="doc1",
        file_storage_id="abc123",
    )

    redact_task = RedactionTask(
        document_id="doc1",
        jurisdiction_id="jur1",
        case_id="case1",
    )

    result = redact.s(fetch_result, redact_task).apply()
    assert (
        result.get().model_dump()
        == RedactionTaskResult(
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            file_storage_id=None,
            errors=[
                ProcessingError(
                    message="No text found in file.",
                    task="redact",
                    exception="EmptyExtractionError",
                )
            ],
            renderer=OutputFormat.PDF,
        ).model_dump()
    )


def test_check_quality_divide_by_zero():
    # Test case where the denominator is zero.
    # Don't raise an error!
    qr = QualityReport()
    assert check_quality(qr) is None
