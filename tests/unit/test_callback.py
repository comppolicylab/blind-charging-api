import responses
from fakeredis import FakeRedis
from responses import matchers

from app.server.generated.models import Document, DocumentLink
from app.server.tasks import (
    CallbackTask,
    CallbackTaskResult,
    FormatTaskResult,
    callback,
)


def test_callback_no_callback_no_error():
    fmt_result = FormatTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        redact_error=None,
        document=Document(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url="http://blob.test.local/abc123",
            )
        ),
    )

    cb = CallbackTask(callback_url=None)

    result = callback.s(fmt_result, cb).apply()
    assert result.get() == CallbackTaskResult(
        status_code=0,
        response="[nothing to do]",
        formatted=fmt_result,
    )


def test_callback_no_callback_with_error():
    # Doesn't matter if the task had an error, if there's no callback we
    # still just pass through.
    fmt_result = FormatTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        redact_error="error",
        document=None,
    )

    cb = CallbackTask(callback_url=None)

    result = callback.s(fmt_result, cb).apply()
    assert result.get() == CallbackTaskResult(
        status_code=0,
        response="[nothing to do]",
        formatted=fmt_result,
    )


@responses.activate
def test_callback_with_callback_no_error(fake_redis_store: FakeRedis):
    fake_redis_store.hset("jur1:case1:mask", mapping={"sub1": "Subject 1"})

    fmt_result = FormatTaskResult(
        jurisdiction_id="jur1",
        case_id="case1",
        document_id="doc1",
        redact_error=None,
        document=Document(
            root=DocumentLink(
                documentId="doc1",
                attachmentType="LINK",
                url="http://blob.test.local/abc123",
            )
        ),
    )

    responses.add(
        responses.POST,
        "http://callback.test.local",
        json={"status": "ok"},
        status=200,
        match=[
            matchers.json_params_matcher(
                {
                    "jurisdictionId": "jur1",
                    "caseId": "case1",
                    "inputDocumentId": "doc1",
                    "maskedSubjects": [{"subjectId": "sub1", "alias": "Subject 1"}],
                    "redactedDocument": {
                        "documentId": "doc1",
                        "attachmentType": "LINK",
                        "url": "http://blob.test.local/abc123",
                    },
                    "status": "COMPLETE",
                }
            ),
        ],
    )

    cb = CallbackTask(callback_url="http://callback.test.local")

    result = callback.s(fmt_result, cb).apply()
    assert result.get() == CallbackTaskResult(
        status_code=200,
        response='{"status": "ok"}',
        formatted=fmt_result,
    )
