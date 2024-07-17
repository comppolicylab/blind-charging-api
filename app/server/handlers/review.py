from dataclasses import dataclass

from fastapi import Request

from ..features import gater
from ..generated.models import BlindReviewInfo, MaskedSubject
from ..store import key


@dataclass
class CaseEntity:
    jurisdiction_id: str
    case_id: str
    subject_id: str | None

    @property
    def id(self) -> str:
        return f"{self.jurisdiction_id}:{self.case_id}"


async def get_blind_review_info(
    request: Request,
    jurisdiction_id: str,
    case_id: str,
    subject_id: str | None,
) -> BlindReviewInfo:
    """Get the blind review info for a case."""
    case_entity = CaseEntity(jurisdiction_id, case_id, subject_id)
    blinded = await gater.ft_blind_review(case_entity)

    masks = {}
    if blinded:
        masks = await request.state.store.hgetall(key(jurisdiction_id, case_id, "mask"))

    masked_subjects = [
        MaskedSubject(subjectId=k, alias=v or "") for k, v in masks.items()
    ]

    return BlindReviewInfo(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        blindReviewRequired=blinded,
        maskedSubjects=masked_subjects,
        redactedDocuments=[],  # TODO - deleted?
    )
