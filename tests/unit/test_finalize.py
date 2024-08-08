from unittest.mock import patch

from app.server.db import DocumentStatus
from app.server.generated.models import Document, DocumentLink
from app.server.tasks import (
    CallbackTaskResult,
    FinalizeTask,
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

    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
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

    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
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

    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
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

    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
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


@patch("app.server.tasks.controller.chain")
def test_finalize_no_experiments_more_objects(chain_mock, config, fake_redis_store):
    config.experiments.enabled = False

    chain_mock.return_value.apply_async.return_value = "new_task_id"

    # Add two docs to the queue. One is the current doc, the other is the next doc.
    fake_redis_store.rpush(
        "jur1:case1:objects",
        '{"callbackUrl": "https://echo/2", "document": '
        '{"attachmentType": "LINK", "documentId": "doc2", '
        '"url": "https://test_document.pdf/"}, "targetBlobUrl": null}',
    )
    fake_redis_store.rpush(
        "jur1:case1:objects",
        '{"callbackUrl": "https://echo/1", "document": '
        '{"attachmentType": "LINK", "documentId": "doc1", '
        '"url": "https://test_document.pdf/"}, "targetBlobUrl": null}',
    )
    # Set a task ID for the current doc
    fake_redis_store.hset("jur1:case1:task", "doc1", "fake_task_id")

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

    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
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
            "next_task_id": "new_task_id",
        }
    )

    # Check that the next doc was processed
    assert fake_redis_store.llen("jur1:case1:objects") == 0
    assert fake_redis_store.hget("jur1:case1:task", "doc2") == b"new_task_id"
