from app.server.db import DocumentStatus
from app.server.generated.models import Document, DocumentLink
from app.server.tasks import (
    CallbackTaskResult,
    FinalizeTaskResult,
    FormatTaskResult,
    ProcessingError,
    finalize,
)


def test_finalize_no_experiments_success(config):
    config.experiments.enabled = False

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=Document(
                root=DocumentLink(
                    documentId="doc1",
                    attachmentType="LINK",
                    url="http://blob.test.local/abc123",
                )
            ),
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[],
        ),
    )

    result = finalize.s(cb).apply()
    assert result.get() == FinalizeTaskResult.model_validate(
        {
            "jurisdiction_id": "jur1",
            "case_id": "case1",
            "document_id": "doc1",
            "document": {
                "documentId": "doc1",
                "attachmentType": "LINK",
                "url": "http://blob.test.local/abc123",
            },
            "errors": [],
        }
    )


def test_finalize_no_experiments_failed(config):
    config.experiments.enabled = False

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=None,
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[
                ProcessingError(message="error", task="task", exception="Exception")
            ],
        ),
    )

    result = finalize.s(cb).apply()
    assert result.get() == FinalizeTaskResult.model_validate(
        {
            "jurisdiction_id": "jur1",
            "case_id": "case1",
            "document_id": "doc1",
            "document": None,
            "errors": [{"message": "error", "task": "task", "exception": "Exception"}],
        }
    )


def test_finalize_experiments_success(config, exp_db):
    config.experiments.enabled = True

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=Document(
                root=DocumentLink(
                    documentId="doc1",
                    attachmentType="LINK",
                    url="http://blob.test.local/abc123",
                )
            ),
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[],
        ),
    )

    result = finalize.s(cb).apply()
    assert result.get() == FinalizeTaskResult.model_validate(
        {
            "jurisdiction_id": "jur1",
            "case_id": "case1",
            "document_id": "doc1",
            "document": {
                "documentId": "doc1",
                "attachmentType": "LINK",
                "url": "http://blob.test.local/abc123",
            },
            "errors": [],
        }
    )

    with exp_db.sync_session() as session:
        ds = (
            session.query(DocumentStatus)
            .filter_by(
                jurisdiction_id="jur1",
                case_id="case1",
                document_id="doc1",
            )
            .all()
        )
        assert len(ds) == 1
        assert ds[0].status == "COMPLETE"
        assert ds[0].error is None


def test_finalize_experiments_failed(config, exp_db):
    config.experiments.enabled = True

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=None,
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[
                ProcessingError(message="error", task="task", exception="Exception")
            ],
        ),
    )

    result = finalize.s(cb).apply()
    assert result.get() == FinalizeTaskResult.model_validate(
        {
            "jurisdiction_id": "jur1",
            "case_id": "case1",
            "document_id": "doc1",
            "document": None,
            "errors": [{"message": "error", "task": "task", "exception": "Exception"}],
        }
    )

    with exp_db.sync_session() as session:
        ds = (
            session.query(DocumentStatus)
            .filter_by(
                jurisdiction_id="jur1",
                case_id="case1",
                document_id="doc1",
            )
            .all()
        )
        assert len(ds) == 1
        assert ds[0].status == "ERROR"
        assert (
            ds[0].error
            == '[{"message": "error", "task": "task", "exception": "Exception"}]'
        )
