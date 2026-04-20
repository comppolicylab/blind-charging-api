import json

import pytest
from bc2.core.common.ontology import (
    Cited,
    Offense,
    PoliceReport,
    PoliceReportParseResult,
    SourceChunk,
    SourceChunkBoundingRegion,
    SourceChunkSpan,
    Subject,
)

from app.server.extracted_report import (
    _build_references,
    _classify_subjects,
    _convert_cited_string,
    _polygon_to_bbox,
    _remap_ids,
    convert_parse_result,
    parse_extracted_report,
)
from app.server.generated.models import (
    BoundingBox,
    CitedString,
    DocumentRegion,
    ExtractedReport,
)


def _cited_str(content: str, ids: list[int] | None = None) -> Cited[str]:
    return Cited[str](ids=ids or [], content=content)


def _subject(type_: str, name: str = "", ids: list[int] | None = None) -> Subject:
    ids = ids or []
    blank = _cited_str("", ids)
    return Subject(
        type=_cited_str(type_, ids),
        name=_cited_str(name, ids) if name else blank,
        address=blank,
        phone=blank,
        race=blank,
        sex=blank,
        dob=blank,
    )


def _chunk(
    content: str,
    regions: list[tuple[int, list[tuple[float, float]]]],
    offset: int = 0,
    length: int = 10,
) -> SourceChunk:
    return SourceChunk(
        spans=[SourceChunkSpan(offset=offset, length=length)],
        regions=[
            SourceChunkBoundingRegion(page=page, points=points)
            for page, points in regions
        ],
        content=content,
    )


def _square(origin_x: float = 0.0, origin_y: float = 0.0, size: float = 1.0):
    return [
        (origin_x, origin_y),
        (origin_x + size, origin_y),
        (origin_x + size, origin_y + size),
        (origin_x, origin_y + size),
    ]


def _build_parse_result(
    *,
    chunks: list[SourceChunk] | None = None,
    subjects: list[Subject] | None = None,
    narratives: list[Cited[str]] | None = None,
    offenses: list[Offense] | None = None,
    reporting_agency: Cited[str] | None = None,
    case_number: Cited[str] | None = None,
    location: Cited[str] | None = None,
    incident_type: Cited[str] | None = None,
) -> PoliceReportParseResult:
    """Convenience builder for PoliceReportParseResult test fixtures."""
    return PoliceReportParseResult(
        report=PoliceReport(
            reporting_agency=reporting_agency or _cited_str(""),
            case_number=case_number or _cited_str(""),
            location=location or _cited_str(""),
            incident_type=incident_type or _cited_str(""),
            subjects=subjects or [],
            narratives=narratives or [],
            offenses=offenses or [],
        ),
        chunks=chunks or [],
    )


# ---------------------------------------------------------------------------
# _polygon_to_bbox
# ---------------------------------------------------------------------------


class TestPolygonToBbox:
    def test_square(self):
        bbox = _polygon_to_bbox(_square())
        assert bbox == BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

    def test_arbitrary_polygon_takes_min_max(self):
        points = [(0.2, 0.9), (0.5, 0.1), (1.3, 0.4), (0.7, 1.2)]
        bbox = _polygon_to_bbox(points)
        assert bbox == BoundingBox(x0=0.2, y0=0.1, x1=1.3, y1=1.2)

    def test_empty_polygon_returns_none(self):
        assert _polygon_to_bbox([]) is None

    def test_single_point(self):
        assert _polygon_to_bbox([(0.5, 0.5)]) == BoundingBox(
            x0=0.5, y0=0.5, x1=0.5, y1=0.5
        )


# ---------------------------------------------------------------------------
# _build_references
# ---------------------------------------------------------------------------


class TestBuildReferences:
    def test_flattens_multi_region_chunks(self):
        chunks = [
            _chunk("a", [(1, _square())]),
            _chunk(
                "b",
                [(1, _square(origin_y=2.0)), (2, _square(origin_x=3.0, size=2.0))],
            ),
            _chunk("c", [(3, _square(size=0.5))]),
        ]
        references, chunk_map = _build_references(chunks)

        assert references == [
            DocumentRegion(page=1, bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)),
            DocumentRegion(page=1, bbox=BoundingBox(x0=0.0, y0=2.0, x1=1.0, y1=3.0)),
            DocumentRegion(page=2, bbox=BoundingBox(x0=3.0, y0=0.0, x1=5.0, y1=2.0)),
            DocumentRegion(page=3, bbox=BoundingBox(x0=0.0, y0=0.0, x1=0.5, y1=0.5)),
        ]
        assert chunk_map == {0: [0], 1: [1, 2], 2: [3]}

    def test_chunk_with_no_regions_maps_to_empty_list(self):
        chunks = [_chunk("lonely", [])]
        references, chunk_map = _build_references(chunks)
        assert references == []
        assert chunk_map == {0: []}

    def test_skips_empty_polygon_regions(self):
        chunks = [_chunk("a", [(1, []), (1, _square())])]
        references, chunk_map = _build_references(chunks)
        # Only the non-empty polygon produces a reference, and the chunk map
        # only references the surviving entry.
        assert len(references) == 1
        assert chunk_map == {0: [0]}

    def test_empty_chunks(self):
        references, chunk_map = _build_references([])
        assert references == []
        assert chunk_map == {}


# ---------------------------------------------------------------------------
# _remap_ids
# ---------------------------------------------------------------------------


class TestRemapIds:
    def test_expands_chunk_ids_into_reference_ids(self):
        chunk_map = {0: [0], 1: [1, 2], 2: [3]}
        assert _remap_ids([0, 1], chunk_map) == [0, 1, 2]

    def test_dedupes_while_preserving_order(self):
        chunk_map = {0: [0, 1], 1: [1, 2], 2: [0]}
        assert _remap_ids([0, 1, 2], chunk_map) == [0, 1, 2]

    def test_unknown_chunk_ids_are_silently_skipped(self):
        chunk_map = {0: [0]}
        assert _remap_ids([99, 0, 42], chunk_map) == [0]

    def test_empty_input(self):
        assert _remap_ids([], {0: [0]}) == []


# ---------------------------------------------------------------------------
# _convert_cited_string
# ---------------------------------------------------------------------------


class TestConvertCitedString:
    def test_basic_conversion_remaps_ids(self):
        cited = _cited_str("hello", ids=[0, 1])
        chunk_map = {0: [0], 1: [1]}
        result = _convert_cited_string(cited, chunk_map)
        assert result == CitedString(referenceIds=[0, 1], content="hello")

    def test_none_in_none_out(self):
        assert _convert_cited_string(None, {}) is None

    def test_empty_content_is_none(self):
        assert _convert_cited_string(_cited_str("", [0]), {0: [0]}) is None

    def test_whitespace_only_content_is_none(self):
        assert _convert_cited_string(_cited_str("   \n\t", [0]), {0: [0]}) is None

    def test_strips_surrounding_whitespace(self):
        result = _convert_cited_string(_cited_str("  hi  ", [0]), {0: [0]})
        assert result is not None
        assert result.content == "hi"


# ---------------------------------------------------------------------------
# _classify_subjects
# ---------------------------------------------------------------------------


class TestClassifySubjects:
    @pytest.mark.parametrize(
        "type_label",
        [
            "Defendant",
            "SUSPECT",
            "accused",
            "arrestee",
            "respondent",
            "perpetrator",
            "offender",
            "Primary Defendant",
            "suspect/arrestee",
        ],
    )
    def test_defendant_keywords(self, type_label):
        defendants, officers, others = _classify_subjects([_subject(type_label, "X")])
        assert len(defendants) == 1
        assert not officers and not others

    @pytest.mark.parametrize(
        "type_label",
        [
            "Officer",
            "Reporting Officer",
            "Deputy Sheriff",
            "Detective",
            "Investigator",
            "POLICE",
            "state trooper",
        ],
    )
    def test_officer_keywords(self, type_label):
        defendants, officers, others = _classify_subjects([_subject(type_label, "X")])
        assert len(officers) == 1
        assert not defendants and not others

    @pytest.mark.parametrize(
        "type_label",
        ["Witness", "Victim", "Complainant", "Bystander", "", "Reporting Party"],
    )
    def test_other_keywords(self, type_label):
        defendants, officers, others = _classify_subjects([_subject(type_label, "X")])
        assert len(others) == 1
        assert not defendants and not officers

    def test_defendant_wins_over_officer_if_both_keywords_present(self):
        # Defendant keyword matching runs before officer matching.
        defendants, officers, _ = _classify_subjects(
            [_subject("Defendant Officer", "X")]
        )
        assert len(defendants) == 1 and not officers


# ---------------------------------------------------------------------------
# convert_parse_result
# ---------------------------------------------------------------------------


class TestConvertParseResult:
    def test_end_to_end_report(self):
        # Three chunks, the second with two regions so we exercise id remap.
        chunks = [
            _chunk("case", [(1, _square())]),
            _chunk(
                "defendant box",
                [(1, _square(origin_y=2.0)), (2, _square(origin_x=3.0))],
            ),
            _chunk("narrative", [(2, _square(origin_y=5.0))]),
        ]
        parse_result = _build_parse_result(
            chunks=chunks,
            reporting_agency=_cited_str("SFPD", [0]),
            case_number=_cited_str("12345", [0]),
            location=_cited_str("100 Main St", [0]),  # dropped by converter
            incident_type=_cited_str("Assault", [0]),  # dropped by converter
            subjects=[
                _subject("Defendant", "John Doe", ids=[1]),
                _subject("Reporting Officer", "Officer Smith", ids=[1]),
                _subject("Witness", "Jane Roe", ids=[1]),
            ],
            narratives=[_cited_str("Fled on foot.", ids=[2])],
            offenses=[
                Offense(
                    crime=_cited_str("Assault", [0]),
                    statute=_cited_str("PC 240", [0]),
                    code=None,
                )
            ],
        )

        result = convert_parse_result(parse_result)

        # Flattened references array.
        assert len(result.references) == 4
        assert result.references[0].page == 1
        assert result.references[3].page == 2

        # Incident metadata: agency + case, but no location / incident_type.
        assert result.incident is not None
        assert result.incident.agencyName == CitedString(
            referenceIds=[0], content="SFPD"
        )
        assert result.incident.incidentNumber == CitedString(
            referenceIds=[0], content="12345"
        )
        assert result.incident.incidentDate is None

        # Defendants have all offenses as charges.
        assert len(result.defendants) == 1
        d = result.defendants[0]
        assert d.name == CitedString(referenceIds=[1, 2], content="John Doe")
        assert len(d.charges) == 1
        charge = d.charges[0]
        assert charge.description == CitedString(referenceIds=[0], content="Assault")
        assert charge.statute == CitedString(referenceIds=[0], content="PC 240")
        assert charge.class_ is None

        # Officer/other classification.
        assert result.referringOfficers is not None
        assert len(result.referringOfficers) == 1
        assert result.referringOfficers[0].name == CitedString(
            referenceIds=[1, 2], content="Officer Smith"
        )
        assert result.otherPeople is not None
        assert len(result.otherPeople) == 1
        assert result.otherPeople[0].name == CitedString(
            referenceIds=[1, 2], content="Jane Roe"
        )
        assert result.otherPeople[0].status == CitedString(
            referenceIds=[1, 2], content="Witness"
        )

        # Narrative ids remap into the flattened references array.
        assert result.narratives == [
            CitedString(referenceIds=[3], content="Fled on foot.")
        ]

    def test_no_offenses_yields_placeholder_empty_charge(self):
        parse_result = _build_parse_result(
            chunks=[_chunk("x", [(1, _square())])],
            subjects=[_subject("Defendant", "D", ids=[0])],
        )
        result = convert_parse_result(parse_result)
        assert len(result.defendants) == 1
        charges = result.defendants[0].charges
        assert len(charges) == 1
        assert charges[0].statute is None
        assert charges[0].description is None
        assert charges[0].severity is None
        assert charges[0].class_ is None

    def test_no_defendant_subjects_synthesizes_placeholder(self):
        parse_result = _build_parse_result(
            chunks=[_chunk("x", [(1, _square())])],
            subjects=[_subject("Witness", "W", ids=[0])],
        )
        result = convert_parse_result(parse_result)
        # Placeholder defendant with no fields populated.
        assert len(result.defendants) == 1
        assert result.defendants[0].name is None
        assert result.defendants[0].charges  # must be non-empty
        # The witness still shows up in otherPeople.
        assert result.otherPeople is not None and len(result.otherPeople) == 1

    def test_empty_optional_lists_are_normalized_to_none(self):
        parse_result = _build_parse_result(
            chunks=[_chunk("x", [(1, _square())])],
            subjects=[_subject("Defendant", "D", ids=[0])],
            narratives=[],
        )
        result = convert_parse_result(parse_result)
        assert result.referringOfficers is None
        assert result.narratives is None
        assert result.otherPeople is None

    def test_offense_code_maps_to_class_field(self):
        parse_result = _build_parse_result(
            chunks=[_chunk("x", [(1, _square())])],
            subjects=[_subject("Defendant", "D", ids=[0])],
            offenses=[
                Offense(
                    crime=_cited_str("Theft", [0]),
                    statute=None,
                    code=_cited_str("459", [0]),
                )
            ],
        )
        result = convert_parse_result(parse_result)
        charge = result.defendants[0].charges[0]
        assert charge.class_ == CitedString(referenceIds=[0], content="459")
        assert charge.statute is None
        assert charge.description == CitedString(referenceIds=[0], content="Theft")

    def test_empty_content_fields_become_none(self):
        parse_result = _build_parse_result(
            chunks=[_chunk("x", [(1, _square())])],
            subjects=[
                Subject(
                    type=_cited_str("Defendant", [0]),
                    name=_cited_str("D", [0]),
                    address=_cited_str("", [0]),
                    phone=_cited_str("   ", [0]),
                    race=_cited_str("", [0]),
                    sex=_cited_str("", [0]),
                    dob=_cited_str("", [0]),
                )
            ],
        )
        result = convert_parse_result(parse_result)
        d = result.defendants[0]
        assert d.name == CitedString(referenceIds=[0], content="D")
        assert d.address is None
        assert d.phoneNumber is None
        assert d.race is None
        assert d.gender is None

    def test_incident_is_present_but_fields_may_be_none(self):
        parse_result = _build_parse_result(
            subjects=[_subject("Defendant", "D")],
        )
        result = convert_parse_result(parse_result)
        assert result.incident is not None
        assert result.incident.agencyName is None
        assert result.incident.incidentNumber is None
        assert result.incident.incidentDate is None


# ---------------------------------------------------------------------------
# parse_extracted_report
# ---------------------------------------------------------------------------


class TestParseExtractedReport:
    @pytest.fixture
    def parse_result_payload(self) -> dict:
        return {
            "chunks": [
                {
                    "spans": [{"offset": 0, "length": 10}],
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
                    "content": "case",
                }
            ],
            "report": {
                "reporting_agency": {"ids": [0], "content": "SFPD"},
                "case_number": {"ids": [0], "content": "12345"},
                "location": {"ids": [], "content": ""},
                "incident_type": {"ids": [], "content": ""},
                "subjects": [
                    {
                        "seq": None,
                        "type": {"ids": [0], "content": "Defendant"},
                        "name": {"ids": [0], "content": "John Doe"},
                        "address": {"ids": [], "content": ""},
                        "phone": {"ids": [], "content": ""},
                        "race": {"ids": [], "content": ""},
                        "sex": {"ids": [], "content": ""},
                        "dob": {"ids": [], "content": ""},
                    }
                ],
                "narratives": [],
                "offenses": [
                    {
                        "crime": {"ids": [0], "content": "Assault"},
                        "statute": None,
                        "code": None,
                    }
                ],
            },
        }

    def test_parses_police_report_parse_result(self, parse_result_payload):
        result = parse_extracted_report(json.dumps(parse_result_payload).encode())
        assert isinstance(result, ExtractedReport)
        assert len(result.references) == 1
        assert result.defendants[0].name == CitedString(
            referenceIds=[0], content="John Doe"
        )

    def test_unwraps_extracted_report_envelope(self, parse_result_payload):
        envelope = {"extractedReport": parse_result_payload}
        result = parse_extracted_report(json.dumps(envelope).encode())
        assert isinstance(result, ExtractedReport)
        assert result.defendants[0].name == CitedString(
            referenceIds=[0], content="John Doe"
        )

    def test_accepts_legacy_extracted_report_payload(self):
        # Already-translated payload (no ``report``/``chunks`` keys) should
        # be accepted verbatim.
        legacy = {
            "references": [
                {"page": 1, "bbox": {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}}
            ],
            "defendants": [
                {
                    "charges": [{}],
                    "name": {"referenceIds": [0], "content": "Jane"},
                }
            ],
        }
        result = parse_extracted_report(json.dumps(legacy).encode())
        assert len(result.references) == 1
        assert result.defendants[0].name == CitedString(
            referenceIds=[0], content="Jane"
        )

    def test_rejects_completely_unrecognized_payload(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            parse_extracted_report(b'{"foo": "bar"}')

    def test_rejects_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_extracted_report(b"not json")
