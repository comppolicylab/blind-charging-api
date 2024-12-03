import pathlib

import responses
from celery import chain
from fakeredis import FakeRedis
from pydantic import AnyUrl
from responses import matchers

from app.server.db import DocumentStatus
from app.server.generated.models import DocumentLink, InputDocument, OutputFormat
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
sample_ocr = sample_data_dir / "simple.ocr.txt"


@responses.activate
def test_chain(fake_redis_store: FakeRedis, exp_db):
    fake_redis_store.hset("jur1:case1:mask", mapping={"sub1": "Subject 1"})

    fd_task_params = FetchTask(
        document=InputDocument(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url=AnyUrl("http://test.local/doc1"),
            ),
        ),
    )

    r_task_params = RedactionTask(
        document_id="doc1",
        jurisdiction_id="jur1",
        case_id="case1",
        renderer=OutputFormat.TEXT,
    )

    cb_task_params = CallbackTask(
        callback_url="http://test.local/callback",
    )

    fmt_task_params = FormatTask()

    ft_task_params = FinalizeTask(
        jurisdiction_id="jur1",
        case_id="case1",
        subject_ids=[],
        renderer=OutputFormat.TEXT,
    )

    responses.add(
        responses.GET,
        "http://test.local/doc1",
        body=sample_pdf.read_bytes(),
        status=200,
    )

    responses.add(
        responses.POST,
        "http://test.local/callback",
        status=200,
        json={"status": "ok"},
        match=[
            matchers.json_params_matcher(
                {
                    "jurisdictionId": "jur1",
                    "caseId": "case1",
                    "inputDocumentId": "doc1",
                    "maskedSubjects": [{"subjectId": "sub1", "alias": "Subject 1"}],
                    "redactedDocument": {
                        "documentId": "doc1",
                        "attachmentType": "BASE64",
                        # NOTE: omitting content since it can vary
                    },
                    "status": "COMPLETE",
                },
                strict_match=False,
            ),
        ],
    )

    chain(
        fetch.s(fd_task_params),
        redact.s(r_task_params),
        format.s(fmt_task_params),
        callback.s(cb_task_params),
        finalize.s(ft_task_params),
    ).apply()

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
