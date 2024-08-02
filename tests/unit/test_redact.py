import pathlib
import re

from app.server.generated.models import OutputFormat
from app.server.tasks import (
    FetchTaskResult,
    ProcessingError,
    RedactionTask,
    RedactionTaskResult,
    redact,
)

this_dir = pathlib.Path(__file__).parent
sample_data_dir = this_dir.parent.parent / "app" / "server" / "sample_data"
sample_pdf = sample_data_dir / "simple.pdf"
sample_ocr = sample_data_dir / "simple.ocr.txt"


def test_redact():
    fetch_result = FetchTaskResult(
        document_id="doc1",
        file_bytes=sample_pdf.read_bytes(),
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
    assert re.sub(r"\s+", "", result.content.decode("utf-8")) == re.sub(
        r"\s+", "", sample_ocr.read_text()
    )
    assert result.model_dump(exclude=["content"]) == {
        "jurisdiction_id": "jur1",
        "case_id": "case1",
        "document_id": "doc1",
        "errors": [],
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
    )


def test_redact_new_errors():
    fetch_result = FetchTaskResult(
        document_id="doc1",
        file_bytes=b"unreadable",
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
            content=None,
            errors=[
                ProcessingError(
                    message="No text found in file.",
                    task="redact",
                    exception="EmptyExtractionError",
                )
            ],
        ).model_dump()
    )
