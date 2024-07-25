from fastapi.testclient import TestClient
from glowplug import DbDriver

from .db import Exposure, ReviewType


async def test_health(api: TestClient):
    response = api.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}


async def test_exposure_blind(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "protocol": "BLIND_REVIEW",
    }
    response = api.post("/api/v1/exposure", json=request)
    assert response.status_code == 200

    with exp_db.sync_session() as sesh:
        exps = sesh.query(Exposure).all()
        assert len(exps) == 1
        exp = exps[0]
        assert exp.jurisdiction_id == "jur1"
        assert exp.case_id == "case1"
        assert exp.subject_id == "sub1"
        assert exp.document_ids == '["doc1"]'
        assert exp.reviewer_id == "att1"
        assert exp.review_type == ReviewType.blind
