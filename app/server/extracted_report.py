"""Translation from bc2 pipeline output to the API ``ExtractedReport`` model.

The extraction pipeline (``bc2.Pipeline``) produces a
:class:`bc2.core.common.ontology.PoliceReportParseResult`, which is
structurally similar to but not identical to the API's
:class:`app.server.generated.models.ExtractedReport`. This module contains the
logic to translate between the two, plus a lenient parser that accepts raw
pipeline output bytes.
"""

import json

from bc2.core.common.ontology import (
    Cited,
    Offense,
    PoliceReportParseResult,
    SourceChunk,
)
from bc2.core.common.ontology import Subject as BC2Subject
from pydantic import ValidationError

from .generated.models import (
    BoundingBox,
    CitedString,
    DocumentRegion,
    ExtractedCharge,
    ExtractedDefendant,
    ExtractedOfficer,
    ExtractedPerson,
    ExtractedReport,
    IncidentMetadata,
)

# Subject `type` values from the ontology prompt are free-form strings, so we
# use lenient keyword matching to bucket them into the API's categories.
_DEFENDANT_TYPE_KEYWORDS = (
    "defendant",
    "suspect",
    "arrestee",
    "accused",
    "respondent",
    "perpetrator",
    "offender",
)
_OFFICER_TYPE_KEYWORDS = (
    "officer",
    "deputy",
    "sheriff",
    "trooper",
    "detective",
    "investigator",
    "police",
)


def parse_extracted_report(raw_output: bytes) -> ExtractedReport:
    """Parse extraction pipeline output into the API model.

    The extraction pipeline produces a :class:`PoliceReportParseResult` from
    ``bc2.core.common.ontology``, which is structurally similar to but not
    identical to :class:`ExtractedReport`. This function translates between the
    two, and also tolerates a couple of alternative envelopes for robustness.
    """
    loaded = json.loads(raw_output)

    # Unwrap common envelopes so we can handle either shape uniformly.
    if isinstance(loaded, dict) and "extractedReport" in loaded:
        loaded = loaded["extractedReport"]

    # The canonical output from the bc2 pipeline is a PoliceReportParseResult
    # (i.e. a dict with "report" and "chunks"). Translate that into the API
    # model. Fall back to treating the payload as an already-translated
    # ExtractedReport to support legacy/alternative pipeline outputs.
    if isinstance(loaded, dict) and "report" in loaded and "chunks" in loaded:
        parse_result = PoliceReportParseResult.model_validate(loaded)
        return convert_parse_result(parse_result)

    try:
        return ExtractedReport.model_validate(loaded)
    except ValidationError:
        # Last-ditch: maybe it's still a parse result but with extra keys.
        return convert_parse_result(PoliceReportParseResult.model_validate(loaded))


def convert_parse_result(parse_result: PoliceReportParseResult) -> ExtractedReport:
    """Translate a bc2 :class:`PoliceReportParseResult` to an ``ExtractedReport``.

    The two schemas describe the same underlying concepts but differ in a few
    ways:

    * bc2 models chunks as polygons (``points``) in arbitrary document regions,
      while the API flattens all cited regions into a single ``references``
      array of axis-aligned bounding boxes. bc2 ``Cited.ids`` refer to chunk
      indices; the API's ``CitedString.referenceIds`` refer to ``references``
      indices. We expand each chunk into one reference per bounding region and
      remap ids accordingly.
    * bc2 ``Subject.type`` is a free-form role label. We bucket subjects into
      defendants, referring officers, and other people via keyword matching.
    * bc2 ``Offense`` fields (``crime``/``statute``/``code``) map to the API's
      ``ExtractedCharge`` fields (``description``/``statute``/``class``).
    * ``PoliceReport.location`` and ``PoliceReport.incident_type`` have no
      natural home in ``ExtractedReport`` and are dropped.
    """
    references, chunk_id_map = _build_references(parse_result.chunks)
    report = parse_result.report

    defendants, officers, others = _classify_subjects(report.subjects)

    charges = [_convert_offense(off, chunk_id_map) for off in report.offenses]
    if not charges:
        # Each defendant must have >=1 charge per the API schema. Fall back to
        # a single empty charge so the payload validates.
        charges = [ExtractedCharge()]

    extracted_defendants = [
        _convert_defendant(subj, charges, chunk_id_map) for subj in defendants
    ]
    if not extracted_defendants:
        # The API requires at least one defendant. Synthesize a placeholder so
        # downstream consumers always see a well-formed payload even when the
        # model failed to identify any defendant-like subject.
        extracted_defendants = [ExtractedDefendant(charges=charges)]

    return ExtractedReport(
        references=references,
        incident=IncidentMetadata(
            agencyName=_convert_cited_string(report.reporting_agency, chunk_id_map),
            incidentNumber=_convert_cited_string(report.case_number, chunk_id_map),
            incidentDate=None,
        ),
        defendants=extracted_defendants,
        referringOfficers=[_convert_officer(subj, chunk_id_map) for subj in officers]
        or None,
        narratives=[
            cs
            for cs in (
                _convert_cited_string(n, chunk_id_map) for n in report.narratives
            )
            if cs is not None
        ]
        or None,
        otherPeople=[_convert_person(subj, chunk_id_map) for subj in others] or None,
    )


def _build_references(
    chunks: list[SourceChunk],
) -> tuple[list[DocumentRegion], dict[int, list[int]]]:
    """Flatten chunk regions into ``references`` and build a chunk→ref id map.

    Each chunk may contribute zero or more regions (one per ``BoundingRegion``).
    The returned map translates a chunk index into the list of flat indices
    into ``references`` for that chunk.
    """
    references: list[DocumentRegion] = []
    chunk_id_map: dict[int, list[int]] = {}
    for chunk_idx, chunk in enumerate(chunks):
        ref_indices: list[int] = []
        for region in chunk.regions:
            bbox = _polygon_to_bbox(region.points)
            if bbox is None:
                continue
            ref_indices.append(len(references))
            references.append(DocumentRegion(page=region.page, bbox=bbox))
        chunk_id_map[chunk_idx] = ref_indices
    return references, chunk_id_map


def _polygon_to_bbox(
    points: list[tuple[float, float]],
) -> BoundingBox | None:
    """Convert a polygon into its axis-aligned bounding box."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def _remap_ids(ids: list[int], chunk_id_map: dict[int, list[int]]) -> list[int]:
    """Expand bc2 chunk ids into flat ``references`` ids, preserving order."""
    out: list[int] = []
    seen: set[int] = set()
    for chunk_id in ids:
        for ref_id in chunk_id_map.get(chunk_id, []):
            if ref_id in seen:
                continue
            seen.add(ref_id)
            out.append(ref_id)
    return out


def _convert_cited_string(
    cited: Cited[str] | None, chunk_id_map: dict[int, list[int]]
) -> CitedString | None:
    """Convert a bc2 ``Cited[str]`` into an API ``CitedString``.

    Returns ``None`` when the value is absent or carries no content, so the
    caller can omit empty optional fields from the output.
    """
    if cited is None:
        return None
    content = (cited.content or "").strip()
    if not content:
        return None
    return CitedString(
        referenceIds=_remap_ids(cited.ids, chunk_id_map),
        content=content,
    )


def _classify_subjects(
    subjects: list[BC2Subject],
) -> tuple[list[BC2Subject], list[BC2Subject], list[BC2Subject]]:
    """Bucket subjects into (defendants, officers, others) by ``type``."""
    defendants: list[BC2Subject] = []
    officers: list[BC2Subject] = []
    others: list[BC2Subject] = []
    for subject in subjects:
        label = (subject.type.content or "").lower()
        if any(kw in label for kw in _DEFENDANT_TYPE_KEYWORDS):
            defendants.append(subject)
        elif any(kw in label for kw in _OFFICER_TYPE_KEYWORDS):
            officers.append(subject)
        else:
            others.append(subject)
    return defendants, officers, others


def _convert_offense(
    offense: Offense, chunk_id_map: dict[int, list[int]]
) -> ExtractedCharge:
    # `class_` on ExtractedCharge is only populable via its `class` alias, so
    # we build the payload as a dict and validate.
    payload: dict = {
        "statute": _convert_cited_string(offense.statute, chunk_id_map),
        "description": _convert_cited_string(offense.crime, chunk_id_map),
        "severity": None,
        "class": _convert_cited_string(offense.code, chunk_id_map),
    }
    return ExtractedCharge.model_validate(payload)


def _convert_defendant(
    subject: BC2Subject,
    charges: list[ExtractedCharge],
    chunk_id_map: dict[int, list[int]],
) -> ExtractedDefendant:
    return ExtractedDefendant(
        charges=charges,
        name=_convert_cited_string(subject.name, chunk_id_map),
        gender=_convert_cited_string(subject.sex, chunk_id_map),
        race=_convert_cited_string(subject.race, chunk_id_map),
        phoneNumber=_convert_cited_string(subject.phone, chunk_id_map),
        address=_convert_cited_string(subject.address, chunk_id_map),
        # bc2 doesn't capture weight/height/eye color for subjects.
        weight=None,
        height=None,
        eyeColor=None,
    )


def _convert_officer(
    subject: BC2Subject, chunk_id_map: dict[int, list[int]]
) -> ExtractedOfficer:
    return ExtractedOfficer(
        name=_convert_cited_string(subject.name, chunk_id_map),
        agency=None,
    )


def _convert_person(
    subject: BC2Subject, chunk_id_map: dict[int, list[int]]
) -> ExtractedPerson:
    return ExtractedPerson(
        name=_convert_cited_string(subject.name, chunk_id_map),
        status=_convert_cited_string(subject.type, chunk_id_map),
    )
