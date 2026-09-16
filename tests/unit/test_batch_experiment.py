"""Batch redaction: one document failing must not stall the rest.

When multiple documents are submitted, finalize iteratively dispatches the next
document after each chain completes. A MissingNarrativeError on an early
document must be recorded as an error for that document and must still allow
remaining documents to be processed.
"""

import importlib
import pathlib

from bc2.core.redact.base import MissingNarrativeError
from celery import chain
from fakeredis import FakeRedis

from app.server.db import DocumentStatus
from app.server.generated.models import (
    DocumentLink,
    InputDocument,
    OutputFormat,
    RedactionTarget,
)
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

this_dir = pathlib.Path(__file__).parent
sample_data_dir = this_dir.parent.parent / "app" / "server" / "sample_data"
sample_pdf = sample_data_dir / "simple.pdf"


def _target(doc_id: str, storage_id: str) -> RedactionTarget:
    return RedactionTarget(
        document=InputDocument(
            root=DocumentLink(
                documentId=doc_id,
                attachmentType="LINK",
                url=f"bcstore://{storage_id}",
            )
        ),
        callbackUrl=None,
        targetBlobUrl=None,
    )


def test_batch_continues_when_first_doc_missing_narrative(
    config, exp_db, fake_redis_store: FakeRedis, monkeypatch
):
    """If doc1 raises MissingNarrativeError, doc2 must still be redacted."""
    config.experiments.enabled = True

    from app.server.tasks.queue import queue as celery_app

    # finalize advances the batch via apply_async(); run those nested chains
    # in-process. Disable eager exception propagation so a single document
    # failure cannot abort the outer chain before finalize runs.
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False

    pdf = sample_pdf.read_bytes()
    # Marker content so the patched pipeline can fail only the first doc.
    fake_redis_store.set("blob_doc1", b"BADDOC")
    fake_redis_store.set("blob_doc2", pdf)

    # Mirror the handler: both objects are queued, and doc1 already has a
    # dispatched task id recorded.
    for obj in (_target("doc1", "blob_doc1"), _target("doc2", "blob_doc2")):
        fake_redis_store.rpush("jur1:case1:objects", obj.model_dump_json(by_alias=True))
    fake_redis_store.hset("jur1:case1:task", "doc1", "fake_task_id")

    redact_mod = importlib.import_module("app.server.tasks.redact")
    real_run = redact_mod.Pipeline.run
    calls = {"n": 0}

    def fake_run(self, runtime_config=None):
        calls["n"] += 1
        buf = (runtime_config or {}).get("in", {}).get("buffer")
        content = buf.getvalue() if buf is not None else b""
        if content == b"BADDOC":
            raise MissingNarrativeError("No narrative text in input.")
        return real_run(self, runtime_config)

    monkeypatch.setattr(redact_mod.Pipeline, "run", fake_run)

    chain(
        fetch.s(FetchTask(document=_target("doc1", "blob_doc1").document)),
        redact.s(
            RedactionTask(
                document_id="doc1",
                jurisdiction_id="jur1",
                case_id="case1",
                renderer=OutputFormat.TEXT,
            )
        ),
        format.s(FormatTask()),
        callback.s(CallbackTask()),
        finalize.s(
            FinalizeTask(
                jurisdiction_id="jur1",
                case_id="case1",
                subject_ids=[],
                renderer=OutputFormat.TEXT,
            )
        ),
    ).apply()

    with exp_db.sync_session() as session:
        statuses = {
            ds.document_id: ds.status for ds in session.query(DocumentStatus).all()
        }

    assert statuses.get("doc1") == "ERROR", (
        f"expected doc1 to fail with missing narrative, got {statuses!r}"
    )
    assert statuses.get("doc2") == "COMPLETE", (
        f"expected remaining doc2 to still be processed after doc1 "
        f"MissingNarrativeError, got {statuses!r}"
    )
    assert fake_redis_store.llen("jur1:case1:objects") == 0
    assert calls["n"] >= 2, (
        f"pipeline should have run for both documents, ran {calls['n']} time(s)"
    )
    assert fake_redis_store.hget("jur1:case1:task", "doc2") is not None
