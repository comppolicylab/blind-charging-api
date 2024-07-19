from dataclasses import dataclass

from fastapi import Request

from ..case import get_aliases
from ..generated.models import BlindReviewInfo, MaskedSubject


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
    blinded = await request.app.state.gater.ft_blind_review(case_entity, deferred=True)

    masked_subjects = list[MaskedSubject]()
    if blinded:
        masked_subjects = await get_aliases(
            request.state.store, jurisdiction_id, case_id
        )

    return BlindReviewInfo(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        blindReviewRequired=blinded,
        maskedSubjects=masked_subjects,
    )
