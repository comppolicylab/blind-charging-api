import base64
import json
from importlib import import_module

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.server.tasks import queue
from tests.integration.testutil import MockCallbackServer


async def test_extract(
    api: TestClient,
    fake_redis_store,
    callback_server: MockCallbackServer,
    monkeypatch: MonkeyPatch,
):
    """An extraction request can be submitted, called back, and polled."""
    extract_task = import_module("app.server.tasks.extract")

    extracted_payload = {
        "chunks": [
            {
                "spans": [{"offset": 0, "length": 8}],
                "regions": [
                    {
                        "page": 1,
                        "points": [
                            [0.0, 0.0],
                            [1.0, 0.0],
                            [1.0, 1.0],
                            [0.0, 1.0],
                        ],
                    }
                ],
                "content": "Jane Doe",
            }
        ],
        "report": {
            "reporting_agency": {"ids": [], "content": ""},
            "case_number": {"ids": [], "content": ""},
            "location": {"ids": [], "content": ""},
            "incident_type": {"ids": [], "content": ""},
            "subjects": [
                {
                    "seq": None,
                    "type": {"ids": [0], "content": "Defendant"},
                    "name": {"ids": [0], "content": "Jane Doe"},
                    "address": {"ids": [], "content": ""},
                    "phone": {"ids": [], "content": ""},
                    "race": {"ids": [], "content": ""},
                    "sex": {"ids": [], "content": ""},
                    "dob": {"ids": [], "content": ""},
                }
            ],
            "narratives": [],
            "offenses": [],
        },
    }

    class FakePipeline:
        def __init__(self, _config):
            pass

        def run(self, buffers):
            buffers["out"]["buffer"].write(json.dumps(extracted_payload).encode())

    # Keep the integration test deterministic and offline while exercising the
    # HTTP handlers, Celery chain, document fetch, result conversion, callback,
    # and polling store.
    monkeypatch.setattr(extract_task, "Pipeline", FakePipeline)
    monkeypatch.setattr(extract_task, "derive_extract_pipeline", lambda _pipe: [])
    monkeypatch.setattr(extract_task, "get_document_sync", lambda _storage_id: b"PDF")
    monkeypatch.setitem(queue.conf, "task_always_eager", True)
    monkeypatch.setitem(queue.conf, "task_store_eager_result", True)
    monkeypatch.setitem(queue.conf, "task_eager_propagates", True)

    response = api.post(
        "/api/v1/extract",
        json={
            "documents": [
                {
                    "document": {
                        "attachmentType": "BASE64",
                        "content": base64.b64encode(b"PDF").decode(),
                    },
                    "callbackUrl": f"{callback_server.base_url}/echo",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert len(response.json()["tokens"]) == 1
    token = response.json()["tokens"][0]

    observed = callback_server.wait_for_request(
        "/echo",
        method="POST",
        json_body={"status": "COMPLETE"},
    )
    callback_body = json.loads(observed.body)
    assert callback_body["extractedReport"]["defendants"][0]["name"] == {
        "referenceIds": [0],
        "content": "Jane Doe",
    }

    status_response = api.get(f"/api/v1/extract/{token}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["token"] == token
    assert status_body["result"]["status"] == "COMPLETE"
    assert status_body["result"]["extractedReport"]["defendants"][0]["name"] == {
        "referenceIds": [0],
        "content": "Jane Doe",
    }
