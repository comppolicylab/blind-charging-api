from datetime import datetime

from fastapi.testclient import TestClient
from glowplug import DbDriver

from app.server.db import Decision, Disqualifier, Exposure, Outcome, ReviewType


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


async def test_exposure_final(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "protocol": "FINAL_REVIEW",
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
        assert exp.review_type == ReviewType.final


async def test_exposure_invalid_protocol(api: TestClient):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "protocol": "INVALID",
    }
    response = api.post("/api/v1/exposure", json=request)
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "ctx": {
                    "expected": "'BLIND_REVIEW' or 'FINAL_REVIEW'",
                },
                "input": "INVALID",
                "loc": [
                    "body",
                    "protocol",
                ],
                "msg": "Input should be 'BLIND_REVIEW' or 'FINAL_REVIEW'",
                "type": "enum",
            },
        ]
    }


async def test_outcome_blind_disqualify(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "decision": {
            "protocol": "BLIND_REVIEW",
            "outcome": {
                "disqualifyingReason": "CASE_TYPE_INELIGIBLE",
                "disqualifyingReasonExplanation": (
                    "This case should not have been selected for blind review."
                ),
                "outcomeType": "DISQUALIFICATION",
            },
        },
        "timestamps": {
            "pageOpen": "2024-07-25T18:12:26.118Z",
            "decision": "2024-07-25T18:12:26.118Z",
        },
    }
    response = api.post("/api/v1/outcome", json=request)
    assert response.status_code == 200

    with exp_db.sync_session() as sesh:
        exps = sesh.query(Outcome).all()
        assert len(exps) == 1
        exp = exps[0]
        assert exp.jurisdiction_id == "jur1"
        assert exp.case_id == "case1"
        assert exp.subject_id == "sub1"
        assert exp.document_ids == '["doc1"]'
        assert exp.reviewer_id == "att1"
        assert exp.review_type == ReviewType.blind
        assert exp.decision == Decision.disqualify
        assert (
            exp.explanation
            == "This case should not have been selected for blind review."
        )
        assert exp.disqualifier == Disqualifier.case_type_ineligible
        assert exp.page_open_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")
        assert exp.decision_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")


async def test_outcome_blind_decline(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "decision": {
            "protocol": "BLIND_REVIEW",
            "outcome": {
                "outcomeType": "BLIND_DECISION",
                "blindChargingDecision": "DECLINE_LIKELY",
                "blindChargingDecisionExplanation": "This case should not be charged.",
                "additionalEvidence": "Some additional evidence.",
            },
        },
        "timestamps": {
            "pageOpen": "2024-07-25T18:12:26.118Z",
            "decision": "2024-07-25T18:12:26.118Z",
        },
    }
    response = api.post("/api/v1/outcome", json=request)
    assert response.status_code == 200

    with exp_db.sync_session() as sesh:
        exps = sesh.query(Outcome).all()
        assert len(exps) == 1
        exp = exps[0]
        assert exp.jurisdiction_id == "jur1"
        assert exp.case_id == "case1"
        assert exp.subject_id == "sub1"
        assert exp.document_ids == '["doc1"]'
        assert exp.reviewer_id == "att1"
        assert exp.review_type == ReviewType.blind
        assert exp.decision == Decision.decline_likely
        assert exp.explanation == "This case should not be charged."
        assert exp.additional_evidence == "Some additional evidence."
        assert exp.disqualifier is None
        assert exp.page_open_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")
        assert exp.decision_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")


async def test_outcome_final_charge(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "decision": {
            "protocol": "FINAL_REVIEW",
            "outcome": {
                "finalChargingDecision": "CHARGE",
                "finalChargingDecisionExplanation": "Charge explanation",
            },
        },
        "timestamps": {
            "pageOpen": "2024-07-25T18:12:26.118Z",
            "decision": "2024-07-25T18:12:26.118Z",
        },
    }
    response = api.post("/api/v1/outcome", json=request)
    assert response.status_code == 200

    with exp_db.sync_session() as sesh:
        exps = sesh.query(Outcome).all()
        assert len(exps) == 1
        exp = exps[0]
        assert exp.jurisdiction_id == "jur1"
        assert exp.case_id == "case1"
        assert exp.subject_id == "sub1"
        assert exp.document_ids == '["doc1"]'
        assert exp.reviewer_id == "att1"
        assert exp.review_type == ReviewType.final
        assert exp.decision == Decision.charge
        assert exp.explanation == "Charge explanation"
        assert exp.additional_evidence is None
        assert exp.disqualifier is None
        assert exp.page_open_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")
        assert exp.decision_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")


async def test_outcome_final_decline(api: TestClient, exp_db: DbDriver):
    request = {
        "jurisdictionId": "jur1",
        "caseId": "case1",
        "subjectId": "sub1",
        "reviewingAttorneyMaskedId": "att1",
        "documentIds": ["doc1"],
        "decision": {
            "protocol": "FINAL_REVIEW",
            "outcome": {
                "finalChargingDecision": "DECLINE",
                "finalChargingDecisionExplanation": "Decline explanation",
            },
        },
        "timestamps": {
            "pageOpen": "2024-07-25T18:12:26.118Z",
            "decision": "2024-07-25T18:12:26.118Z",
        },
    }
    response = api.post("/api/v1/outcome", json=request)
    assert response.status_code == 200

    with exp_db.sync_session() as sesh:
        exps = sesh.query(Outcome).all()
        assert len(exps) == 1
        exp = exps[0]
        assert exp.jurisdiction_id == "jur1"
        assert exp.case_id == "case1"
        assert exp.subject_id == "sub1"
        assert exp.document_ids == '["doc1"]'
        assert exp.reviewer_id == "att1"
        assert exp.review_type == ReviewType.final
        assert exp.decision == Decision.decline
        assert exp.explanation == "Decline explanation"
        assert exp.additional_evidence is None
        assert exp.disqualifier is None
        assert exp.page_open_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")
        assert exp.decision_ts == datetime.fromisoformat("2024-07-25T18:12:26.118")
