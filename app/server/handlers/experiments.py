import json

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ..config import config
from ..db import (
    Decision,
    Disqualifier,
    Exposure,
    Outcome,
    OutcomeDisqualifiers,
    ReviewType,
)
from ..generated.models import (
    BlindChargingDecision,
    BlindDecisionOutcome,
    BlindReviewDecision,
    DisqualifyingReason,
    DisqualifyOutcome,
    FinalChargingDecision,
    FinalReviewDecision,
    Review,
    ReviewDecision,
    ReviewProtocol,
)
from ..generated.models import Exposure as ExposureModel


def review_protocol_to_review_type(protocol: ReviewProtocol) -> ReviewType:
    """Convert a ReviewProtocol to a ReviewType."""
    match protocol:
        case ReviewProtocol.BLIND_REVIEW:
            return ReviewType.blind
        case ReviewProtocol.FINAL_REVIEW:
            return ReviewType.final
        case _:
            raise ValueError(f"Unknown protocol: {protocol}")


async def log_exposure(request: Request, body: ExposureModel) -> None:
    """Record an exposure event to the database."""
    if not config.experiments.enabled:
        return

    subject_ids = (
        body.subjectId if isinstance(body.subjectId, list) else [body.subjectId]
    )
    for subject_id in subject_ids:
        exp = Exposure(
            jurisdiction_id=body.jurisdictionId,
            case_id=body.caseId,
            subject_id=subject_id,
            extra=body.extra,
            document_ids=json.dumps(body.documentIds),
            reviewer_id=body.reviewingAttorneyMaskedId,
            review_type=review_protocol_to_review_type(body.protocol),
        )
        request.state.db.add(exp)
    return


async def log_outcome(request: Request, body: Review) -> None:
    """Record an outcome event to the database."""
    if not config.experiments.enabled:
        return

    try:
        decision_params = format_review_decision(body.decision)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    decision_params_dict = decision_params.model_dump()

    disqualifiers = decision_params_dict.pop("disqualifiers", [])

    subject_ids = (
        body.subjectId if isinstance(body.subjectId, list) else [body.subjectId]
    )
    for subject_id in subject_ids:
        outcome = Outcome(
            jurisdiction_id=body.jurisdictionId,
            case_id=body.caseId,
            subject_id=subject_id,
            reviewer_id=body.reviewingAttorneyMaskedId,
            document_ids=json.dumps(body.documentIds),
            page_open_ts=body.timestamps.pageOpen,
            decision_ts=body.timestamps.decision,
            disqualifiers=[OutcomeDisqualifiers(disqualifier=d) for d in disqualifiers],
            **decision_params_dict,
        )

        request.state.db.add(outcome)
    return


class OutcomeDecision(BaseModel):
    """Subset of Outcome model fields that are relevant to the decision."""

    review_type: ReviewType
    decision: Decision
    explanation: str | None
    disqualifiers: list[Disqualifier] = []
    additional_evidence: str | None = None


def infer_review_type_from_decision(decision: ReviewDecision) -> ReviewType:
    """Infer the ReviewType from a ReviewDecision.

    Args:
        decision (ReviewDecision): The decision to infer the ReviewType from.

    Returns:
        ReviewType: The inferred ReviewType.
    """
    if isinstance(decision.root, BlindReviewDecision):
        return ReviewType.blind
    elif isinstance(decision.root, FinalReviewDecision):
        return ReviewType.final
    else:
        raise ValueError(f"Unknown decision type: {type(decision)}")


def final_decision_to_decision(final_decision: FinalChargingDecision) -> Decision:
    """Convert a FinalChargingDecision to a Decision."""
    match final_decision:
        case FinalChargingDecision.CHARGE:
            return Decision.charge
        case FinalChargingDecision.DECLINE:
            return Decision.decline
        case _:
            raise ValueError(f"Unknown final decision: {final_decision}")


def blind_decision_to_decision(blind_decision: BlindChargingDecision) -> Decision:
    """Convert a BlindChargingDecision to a Decision."""
    match blind_decision:
        case BlindChargingDecision.CHARGE_LIKELY:
            return Decision.charge_likely
        case BlindChargingDecision.CHARGE_MAYBE:
            return Decision.charge_maybe
        case BlindChargingDecision.DECLINE_MAYBE:
            return Decision.decline_likely
        case BlindChargingDecision.DECLINE_LIKELY:
            return Decision.decline_likely
        case _:
            raise ValueError(f"Unknown blind decision: {blind_decision}")


def disqualifying_reason_to_disqualifier(reason: DisqualifyingReason) -> Disqualifier:
    match reason:
        case DisqualifyingReason.ASSIGNED_TO_UNBLIND:
            return Disqualifier.assigned_to_unblind
        case DisqualifyingReason.CASE_TYPE_INELIGIBLE:
            return Disqualifier.case_type_ineligible
        case DisqualifyingReason.PRIOR_KNOWLEDGE_BIAS:
            return Disqualifier.prior_knowledge_bias
        case DisqualifyingReason.NARRATIVE_INCOMPLETE:
            return Disqualifier.narrative_incomplete
        case DisqualifyingReason.REDACTION_MISSING:
            return Disqualifier.redaction_missing
        case DisqualifyingReason.REDACTION_ILLEGIBLE:
            return Disqualifier.redaction_illegible
        case DisqualifyingReason.OTHER:
            return Disqualifier.other
        case _:
            raise ValueError(f"Unknown disqualifying reason: {reason}")


def disqualifying_reason_to_disqualifiers(
    reason: DisqualifyingReason | list[DisqualifyingReason],
) -> list[Disqualifier]:
    """Convert a DisqualifyingReason to a Disqualifier."""
    reasons = [reason] if isinstance(reason, DisqualifyingReason) else reason
    return [disqualifying_reason_to_disqualifier(r) for r in reasons]


def format_blind_review_outcome(
    outcome: BlindDecisionOutcome | DisqualifyOutcome,
) -> OutcomeDecision:
    """Format a BlindReviewDecision outcome into an OutcomeDecision."""
    if isinstance(outcome, BlindDecisionOutcome):
        return OutcomeDecision(
            review_type=ReviewType.blind,
            decision=blind_decision_to_decision(outcome.blindChargingDecision),
            explanation=outcome.blindChargingDecisionExplanation,
            additional_evidence=outcome.additionalEvidence,
        )
    elif isinstance(outcome, DisqualifyOutcome):
        decision = OutcomeDecision(
            review_type=ReviewType.blind,
            decision=Decision.disqualify,
            explanation=outcome.disqualifyingReasonExplanation,
            disqualifiers=disqualifying_reason_to_disqualifiers(
                outcome.disqualifyingReason
            ),
        )

        # The auto-generated Pydantic model doesn't currently respect the minItems
        # constraint from the schema, so we need to enforce it manually.
        if not decision.disqualifiers:
            raise ValueError("DisqualifyOutcome must have at least one disqualifier")

        return decision

    # This should never happen! throw an error just in case.
    raise ValueError(f"Unknown outcome type: {type(outcome)}")


def format_review_decision(decision: ReviewDecision) -> OutcomeDecision:
    """Format a ReviewDecision into an OutcomeDecision."""
    if isinstance(decision.root, BlindReviewDecision):
        return format_blind_review_outcome(decision.root.outcome)
    elif isinstance(decision.root, FinalReviewDecision):
        return OutcomeDecision(
            review_type=ReviewType.final,
            decision=final_decision_to_decision(
                decision.root.outcome.finalChargingDecision
            ),
            explanation=decision.root.outcome.finalChargingDecisionExplanation,
        )
    else:
        raise ValueError(f"Unknown decision type: {type(decision)}")
