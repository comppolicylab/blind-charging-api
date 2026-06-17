import json
from unittest.mock import patch

from celery.result import AsyncResult
from fakeredis import FakeRedis

from app.server.db import DocumentStatus
from app.server.generated.models import DocumentLink, OutputDocument
from app.server.tasks import (
    CallbackTaskResult,
    FinalizeTask,
    FinalizeTaskResult,
    FormatTaskResult,
    ProcessingError,
    finalize,
)


def _seed_result_doc(fake_redis_store: FakeRedis, doc_id: str = "doc1") -> None:
    """Pre-populate the result store with a successful redacted document.

    `finalize` verifies the result doc actually landed in the store before
    declaring success, so tests that simulate a successful chain need to
    seed the same key that `format` would have written.
    """
    doc = OutputDocument(
        root=DocumentLink(
            documentId=doc_id,
            attachmentType="LINK",
            url="http://blob.test.local/abc123",
        )
    )
    fake_redis_store.set(f"jur1:case1:result:{doc_id}", doc.model_dump_json())


def test_finalize_no_experiments_success(config, fake_redis_store: FakeRedis):
    config.experiments.enabled = False
    _seed_result_doc(fake_redis_store)

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=OutputDocument(
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


def test_finalize_experiments_success(config, exp_db, fake_redis_store: FakeRedis):
    config.experiments.enabled = True
    _seed_result_doc(fake_redis_store)

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=OutputDocument(
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


def test_finalize_detects_missing_result_doc(
    config, exp_db, fake_redis_store: FakeRedis
):
    """When the pipeline reports no errors but the result doc is absent
    from the store, finalize should:
      * append a ``finalize.verify_result`` ProcessingError,
      * write ``status="ERROR"`` to the experiments DB,
      * return that error in ``FinalizeTaskResult.errors``.
    This keeps the DB and the poll API in agreement.
    """
    config.experiments.enabled = True

    # NB: no `jur1:case1:result:doc1` key is set in fake_redis_store --
    # this simulates a write that silently failed or a key that was
    # evicted before finalize ran.

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
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
    final = result.get()

    assert isinstance(final, FinalizeTaskResult)
    assert len(final.errors) == 1
    assert final.errors[0].task == "finalize.verify_result"
    assert final.errors[0].exception == "MissingResultDocument"

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
        # Error JSON should mention the verify task so operators can
        # tell this apart from upstream pipeline failures.
        assert ds[0].error is not None
        recorded = json.loads(ds[0].error)
        assert recorded[0]["task"] == "finalize.verify_result"


def test_finalize_writes_complete_when_result_doc_present(
    config, exp_db, fake_redis_store: FakeRedis
):
    """When the pipeline reports no errors *and* the result doc is present
    in the store, finalize should write ``status="COMPLETE"`` and not
    invent a spurious error.
    """
    config.experiments.enabled = True

    doc = OutputDocument(
        root=DocumentLink(
            documentId="doc1",
            attachmentType="LINK",
            url="http://blob.test.local/abc123",
        )
    )
    fake_redis_store.set("jur1:case1:result:doc1", doc.model_dump_json())

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
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
    final = result.get()

    assert isinstance(final, FinalizeTaskResult)
    assert final.errors == []

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


def test_finalize_preserves_upstream_errors_without_verifying(
    config, exp_db, fake_redis_store: FakeRedis
):
    """If upstream already recorded errors, the verify-result probe should
    be skipped (we already know the pipeline failed) and the existing
    errors should be reported as-is.
    """
    config.experiments.enabled = True

    upstream_err = ProcessingError(
        message="Boom",
        task="format",
        exception="ValueError",
    )

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            jurisdiction_id="jur1",
            case_id="case1",
            document_id="doc1",
            errors=[upstream_err],
        ),
    )
    ft = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer="PDF",
    )

    result = finalize.s(cb, ft).apply()
    final = result.get()

    assert isinstance(final, FinalizeTaskResult)
    # No "finalize.verify_result" error layered on top.
    assert len(final.errors) == 1
    assert final.errors[0].task == "format"

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


@patch("app.server.tasks.finalize.config.experiments.store.driver.sync_session")
@patch("app.server.tasks.controller.chain")
def test_finalize_status_write_failure_still_processes_next_object(
    chain_mock, sync_session_mock, config, exp_db, fake_redis_store
):
    config.experiments.enabled = True
    _seed_result_doc(fake_redis_store)
    sync_session_mock.side_effect = RuntimeError("db unavailable")
    chain_mock.return_value.apply_async.return_value = AsyncResult("new_task_id")

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
    fake_redis_store.hset("jur1:case1:task", "doc1", "fake_task_id")

    cb = CallbackTaskResult(
        status_code=200,
        response="ok",
        formatted=FormatTaskResult(
            document=OutputDocument(
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
    assert fake_redis_store.llen("jur1:case1:objects") == 0
    assert fake_redis_store.hget("jur1:case1:task", "doc2") == b"new_task_id"


@patch("app.server.tasks.controller.chain")
def test_finalize_no_experiments_more_objects(chain_mock, config, fake_redis_store):
    config.experiments.enabled = False
    _seed_result_doc(fake_redis_store)

    chain_mock.return_value.apply_async.return_value = AsyncResult("new_task_id")

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
            document=OutputDocument(
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
