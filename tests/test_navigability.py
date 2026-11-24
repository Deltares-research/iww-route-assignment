"""Navigability evaluator tests. Every expected outcome is checked by hand in the comment."""

from dataclasses import replace
from math import inf, nan
from pathlib import Path

import numpy as np
import pytest

from iww_route_assignment import (
    ArcNavigability,
    ArcProfile,
    ArcRestriction,
    NavigabilityFinding,
    NavigabilityMargins,
    NavigabilityStatus,
    NavigabilityVessel,
    RestrictionType,
    RouteNavigability,
    RouteSequence,
    StructureType,
    ValidationError,
    build_restriction_index,
    evaluate_arc,
    evaluate_route,
    navigable_arc_mask,
    restrict_choice_weights,
)
from iww_route_assignment.io import (
    load_arc_profiles_csv,
    load_arc_restrictions_csv,
    load_navigability_vessels_csv,
    load_route_sequences_csv,
    load_vessel_references_csv,
    route_navigability_to_frames,
)

FIXTURES = Path(__file__).parent / "fixtures"
NAV = NavigabilityStatus.NAVIGABLE
UNDET = NavigabilityStatus.UNDETERMINED
BLOCKED = NavigabilityStatus.NOT_NAVIGABLE


def vessel(**changes: object) -> NavigabilityVessel:
    base = NavigabilityVessel("v", length_m=110.0, beam_m=11.4, draught_m=3.0, air_draught_m=6.0)
    return replace(base, **changes)  # type: ignore[arg-type]


def restriction(**changes: object) -> ArcRestriction:
    base = ArcRestriction("a1", "s1", StructureType.LOCK, "test")
    return replace(base, **changes)  # type: ignore[arg-type]


def kinds(result: ArcNavigability) -> list[RestrictionType]:
    return [finding.restriction for finding in result.findings]


def arc_profile(arc_id: str, start: str, end: str) -> ArcProfile:
    return ArcProfile(arc_id, start, end, 1.0, "Va", 10.0, "test")


# Contracts -------------------------------------------------------------------------------


def test_restriction_normalises_text_and_string_enum() -> None:
    row = ArcRestriction(" a1 ", " s1 ", "bridge", " src ")  # type: ignore[arg-type]
    assert (row.arc_id, row.structure_id, row.source) == ("a1", "s1", "src")
    assert row.structure_type is StructureType.BRIDGE


@pytest.mark.parametrize(
    "changes",
    [
        {"arc_id": " "},
        {"structure_type": "canal"},
        {"max_length_m": 0.0},
        {"max_beam_m": -1.0},
        {"max_draught_m": nan},
        {"max_air_draught_m": inf},
        {"is_open": "yes"},
        {"dangerous_goods_permitted": 1},
    ],
)
def test_restriction_rejects_invalid_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        restriction(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"vessel_id": ""},
        {"length_m": 0.0},
        {"beam_m": nan},
        {"draught_m": -2.0},
        {"air_draught_m": 0.0},
        {"carries_dangerous_goods": "no"},
    ],
)
def test_vessel_rejects_invalid_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        vessel(**changes)


@pytest.mark.parametrize(
    "changes",
    [{"under_keel_clearance_m": -0.1}, {"air_clearance_m": nan}, {"beam_clearance_m": inf}],
)
def test_margins_reject_invalid_values(changes: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        NavigabilityMargins(**changes)


def test_finding_rejects_navigable_status_and_non_finite_values() -> None:
    with pytest.raises(ValidationError, match="undetermined or not navigable"):
        NavigabilityFinding("a", RestrictionType.LENGTH, NAV, "s")
    with pytest.raises(ValidationError, match="required_m"):
        NavigabilityFinding("a", RestrictionType.LENGTH, BLOCKED, "s", required_m=nan)
    with pytest.raises(ValidationError, match="status"):
        NavigabilityFinding("a", RestrictionType.LENGTH, "bad", "s")  # type: ignore[arg-type]


def test_vessel_from_reference_uses_loading_state() -> None:
    reference = load_vessel_references_csv(FIXTURES / "vessel_references.csv")[0]
    loaded = NavigabilityVessel.from_reference(reference, is_loaded=True, air_draught_m=5.0)
    empty = NavigabilityVessel.from_reference(
        reference, is_loaded=False, carries_dangerous_goods=True, vessel_id="x"
    )
    assert loaded.draught_m == reference.draught_loaded_m
    assert loaded.vessel_id == f"{reference.vessel_rws_class}-loaded"
    assert loaded.source.endswith(":loaded")
    assert (empty.vessel_id, empty.draught_m) == ("x", reference.draught_empty_m)
    assert empty.carries_dangerous_goods
    with pytest.raises(ValidationError):
        NavigabilityVessel.from_reference(reference, is_loaded=1)  # type: ignore[arg-type]


# Single structure checks -----------------------------------------------------------------


def test_unrestricted_structure_is_navigable() -> None:
    index = build_restriction_index([restriction()])
    assert evaluate_arc("a1", vessel(), index).status is NAV


@pytest.mark.parametrize(
    ("field", "limit", "vessel_field", "value", "expected"),
    [
        # Equality is navigable: 110.0 <= 110.0.
        ("max_length_m", 110.0, "length_m", 110.0, NAV),
        ("max_length_m", 110.0, "length_m", 110.01, BLOCKED),
        ("max_beam_m", 11.4, "beam_m", 11.4, NAV),
        ("max_beam_m", 11.4, "beam_m", 11.45, BLOCKED),
        ("max_draught_m", 3.0, "draught_m", 3.0, NAV),
        ("max_draught_m", 3.0, "draught_m", 3.01, BLOCKED),
        ("max_air_draught_m", 6.0, "air_draught_m", 6.0, NAV),
        ("max_air_draught_m", 6.0, "air_draught_m", 6.2, BLOCKED),
    ],
)
def test_dimension_boundaries(
    field: str, limit: float, vessel_field: str, value: float, expected: NavigabilityStatus
) -> None:
    index = build_restriction_index([restriction(**{field: limit})])
    assert evaluate_arc("a1", vessel(**{vessel_field: value}), index).status is expected


def test_blocking_finding_records_required_and_limit_values() -> None:
    # Draught 3.2 + keel clearance 0.3 = 3.5 > 3.4.
    index = build_restriction_index([restriction(max_draught_m=3.4)])
    margins = NavigabilityMargins(under_keel_clearance_m=0.3)
    (finding,) = evaluate_arc("a1", vessel(draught_m=3.2), index, margins).findings
    assert finding.restriction is RestrictionType.DRAUGHT
    assert finding.status is BLOCKED
    assert finding.required_m == pytest.approx(3.5)
    assert finding.limit_m == 3.4
    assert (finding.structure_id, finding.structure_type) == ("s1", StructureType.LOCK)


@pytest.mark.parametrize(
    ("margins", "expected"),
    [
        # 2.8 + 0.2 = 3.0 equals the limit after binary rounding; tolerance keeps it navigable.
        (NavigabilityMargins(under_keel_clearance_m=0.2), NAV),
        (NavigabilityMargins(under_keel_clearance_m=0.21), BLOCKED),
    ],
)
def test_margin_boundary_uses_tolerance(
    margins: NavigabilityMargins, expected: NavigabilityStatus
) -> None:
    index = build_restriction_index([restriction(max_draught_m=3.0)])
    assert evaluate_arc("a1", vessel(draught_m=2.8), index, margins).status is expected


def test_air_and_beam_margins_apply() -> None:
    index = build_restriction_index([restriction(max_air_draught_m=6.5, max_beam_m=11.5)])
    # Air 6.0 + 0.6 = 6.6 > 6.5 and beam 11.4 + 0.2 = 11.6 > 11.5.
    margins = NavigabilityMargins(air_clearance_m=0.6, beam_clearance_m=0.2)
    result = evaluate_arc("a1", vessel(), index, margins)
    assert kinds(result) == [RestrictionType.BEAM, RestrictionType.AIR_DRAUGHT]


def test_length_has_no_margin() -> None:
    index = build_restriction_index([restriction(max_length_m=110.0)])
    margins = NavigabilityMargins(0.5, 0.5, 0.5)
    assert evaluate_arc("a1", vessel(), index, margins).status is NAV


def test_unknown_air_draught_under_bridge_is_undetermined() -> None:
    index = build_restriction_index([restriction(max_air_draught_m=7.0)])
    (finding,) = evaluate_arc("a1", vessel(air_draught_m=None), index).findings
    assert (finding.status, finding.restriction) == (UNDET, RestrictionType.AIR_DRAUGHT)
    assert finding.required_m is None and finding.limit_m == 7.0


def test_unknown_air_draught_without_height_limit_is_navigable() -> None:
    index = build_restriction_index([restriction(max_draught_m=4.0)])
    assert evaluate_arc("a1", vessel(air_draught_m=None), index).status is NAV


def test_closed_structure_blocks_any_vessel() -> None:
    index = build_restriction_index([restriction(structure_type=StructureType.WEIR, is_open=False)])
    result = evaluate_arc("a1", vessel(), index)
    assert result.status is BLOCKED and kinds(result) == [RestrictionType.CLOSED]


def test_dangerous_goods_block_only_carrying_vessels() -> None:
    index = build_restriction_index([restriction(dangerous_goods_permitted=False)])
    assert evaluate_arc("a1", vessel(), index).status is NAV
    result = evaluate_arc("a1", vessel(carries_dangerous_goods=True), index)
    assert kinds(result) == [RestrictionType.DANGEROUS_GOODS]


def test_blocking_outranks_undetermined_and_all_findings_are_kept() -> None:
    index = build_restriction_index(
        [
            restriction(structure_id="bridge", max_air_draught_m=5.0),
            restriction(structure_id="lock", max_length_m=100.0),
        ]
    )
    result = evaluate_arc("a1", vessel(air_draught_m=None), index)
    assert result.status is BLOCKED
    assert sorted(kinds(result)) == [RestrictionType.AIR_DRAUGHT, RestrictionType.LENGTH]


# Coverage and index ----------------------------------------------------------------------


def test_uncovered_arc_is_undetermined_unless_coverage_waived() -> None:
    index = build_restriction_index([])
    result = evaluate_arc("missing", vessel(), index)
    assert result.status is UNDET
    assert kinds(result) == [RestrictionType.COVERAGE]
    assert result.findings[0].source == "restriction_index"
    assert evaluate_arc("missing", vessel(), index, require_coverage=False).status is NAV


def test_duplicate_structure_on_arc_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate restriction"):
        build_restriction_index([restriction(), restriction(max_length_m=50.0)])


def test_same_structure_on_different_arcs_is_allowed() -> None:
    index = build_restriction_index([restriction(), restriction(arc_id="a2")])
    assert set(index) == {"a1", "a2"}


def test_index_and_findings_do_not_depend_on_input_order() -> None:
    rows = [
        restriction(structure_id="b", structure_type=StructureType.BRIDGE, max_air_draught_m=5),
        restriction(structure_id="a", structure_type=StructureType.LOCK, max_length_m=100),
        restriction(arc_id="a0", structure_id="w", structure_type=StructureType.WATERWAY),
    ]
    forward = build_restriction_index(rows)
    backward = build_restriction_index(reversed(rows))
    assert list(forward) == list(backward) == ["a0", "a1"]
    assert forward["a1"] == backward["a1"]
    assert evaluate_arc("a1", vessel(), forward) == evaluate_arc("a1", vessel(), backward)


def test_index_is_read_only() -> None:
    index = build_restriction_index([restriction()])
    with pytest.raises(TypeError):
        index["x"] = ()  # type: ignore[index]


# Routes ----------------------------------------------------------------------------------


def test_route_status_and_first_blocking_sequence() -> None:
    route = RouteSequence(
        "r",
        "nlaaa",
        "nlddd",
        (arc_profile("ab", "a", "b"), arc_profile("bc", "b", "c"), arc_profile("cd", "c", "d")),
        "test",
    )
    index = build_restriction_index(
        [
            restriction(arc_id="ab"),
            restriction(arc_id="bc", max_draught_m=2.0),
            restriction(arc_id="cd", max_length_m=50.0),
        ]
    )
    result = evaluate_route(route, vessel(), index)
    assert result.status is BLOCKED
    assert [arc.status for arc in result.arcs] == [NAV, BLOCKED, BLOCKED]
    assert result.first_blocking_sequence == 1
    assert len(result.findings) == 2


def test_repeated_arcs_are_reported_per_traversal() -> None:
    route = RouteSequence(
        "loop",
        "a",
        "a",
        (arc_profile("ab", "a", "b"), arc_profile("ba", "b", "a"), arc_profile("ab", "a", "b")),
        "test",
    )
    index = build_restriction_index([restriction(arc_id="ab", is_open=False)])
    result = evaluate_route(route, vessel(), index, require_coverage=False)
    assert [arc.arc_id for arc in result.arcs] == ["ab", "ba", "ab"]
    assert [arc.status for arc in result.arcs] == [BLOCKED, NAV, BLOCKED]


def test_navigable_route_has_no_blocking_sequence() -> None:
    route = RouteSequence("r", "a", "b", (arc_profile("ab", "a", "b"),), "test")
    result = evaluate_route(route, vessel(), build_restriction_index([restriction(arc_id="ab")]))
    assert result.status is NAV and result.first_blocking_sequence is None
    assert result.findings == ()


def test_empty_route_navigability_is_navigable() -> None:
    assert RouteNavigability("r", "v", ()).status is NAV


# Mask and separation from costs ----------------------------------------------------------


def test_mask_follows_status_and_undetermined_option() -> None:
    index = build_restriction_index(
        [
            restriction(arc_id="ok"),
            restriction(arc_id="low", max_air_draught_m=5.0),
            restriction(arc_id="shut", is_open=False),
        ]
    )
    ids = ["ok", "low", "shut", "none"]
    unknown_air = vessel(air_draught_m=None)
    assert navigable_arc_mask(ids, unknown_air, index).tolist() == [True, False, False, False]
    assert navigable_arc_mask(ids, unknown_air, index, allow_undetermined=True).tolist() == [
        True,
        True,
        False,
        True,
    ]
    assert navigable_arc_mask([], vessel(), index).shape == (0,)


def test_no_cost_can_override_a_blocked_arc() -> None:
    weights = restrict_choice_weights([0.0, 5.0, 1e300], np.array([False, True, False]))
    assert weights.tolist() == [inf, 5.0, inf]


@pytest.mark.parametrize(
    ("weights", "mask", "message"),
    [
        ([1.0], [1], "boolean"),
        ([1.0, 2.0], [True], "shape"),
        ([nan], [True], "finite"),
        ([-1.0], [True], "non-negative"),
    ],
)
def test_choice_weight_validation(weights: list[float], mask: list[object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        restrict_choice_weights(weights, np.array(mask))


def test_navigability_package_does_not_import_costs() -> None:
    package = Path(__file__).parents[1] / "src" / "iww_route_assignment" / "navigability"
    imports = [
        line
        for path in package.glob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("from ", "import "))
    ]
    assert imports and not any("costs" in line for line in imports)


# File adapters ---------------------------------------------------------------------------


def test_fixture_case_end_to_end() -> None:
    arcs = load_arc_profiles_csv(FIXTURES / "arc_profiles.csv")
    routes = {
        route.route_id: route
        for route in load_route_sequences_csv(FIXTURES / "route_sequences.csv", arcs)
    }
    index = build_restriction_index(load_arc_restrictions_csv(FIXTURES / "arc_restrictions.csv"))
    vessels = {
        row.vessel_id: row
        for row in load_navigability_vessels_csv(FIXTURES / "navigability_vessels.csv")
    }
    results = [
        evaluate_route(routes[route_id], vessels[vessel_id], index)
        for route_id in ("ab-route", "bc-route")
        for vessel_id in ("m8-loaded", "m8-empty", "m6-tanker")
    ]
    # ab: draught limit 3.5, bridge 7.0. bc: draught limit 2.8, lock without dangerous goods.
    assert [result.status for result in results] == [
        NAV,  # 3.5 <= 3.5 and 6.5 <= 7.0
        UNDET,  # unknown air draught under the bridge
        NAV,  # 2.5 <= 3.5 and 6.0 <= 7.0
        BLOCKED,  # 3.5 > 2.8
        NAV,  # 1.4 <= 2.8; lock 110 x 11.4 fits exactly
        BLOCKED,  # dangerous goods at the lock
    ]
    summary, findings = route_navigability_to_frames(results)
    assert summary["status"].tolist() == [result.status.value for result in results]
    assert summary["first_blocking_sequence"].isna().tolist() == [
        True,
        True,
        True,
        False,
        True,
        False,
    ]
    assert findings["restriction"].tolist() == ["air_draught", "draught", "dangerous_goods"]
    assert findings.loc[1, "required_m"] == 3.5 and findings.loc[1, "limit_m"] == 2.8


def test_frames_for_coverage_finding_and_empty_input() -> None:
    route = RouteSequence("r", "a", "b", (arc_profile("ab", "a", "b"),), "test")
    result = evaluate_route(route, vessel(), build_restriction_index([]))
    _, findings = route_navigability_to_frames([result])
    assert findings.loc[0, "structure_type"] is None
    assert findings.loc[0, "restriction"] == "coverage"
    summary, findings = route_navigability_to_frames([])
    assert summary.empty and findings.empty
    assert list(findings.columns)[:3] == ["route_id", "vessel_id", "sequence"]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "table.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_restriction_loader_parses_flags_case_insensitively(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "arc_id,structure_id,structure_type,is_open,dangerous_goods_permitted,source\n"
        "a,s,LOCK,No,YES,src\n",
    )
    (row,) = load_arc_restrictions_csv(path)
    assert row.structure_type is StructureType.LOCK
    assert row.is_open is False and row.dangerous_goods_permitted is True
    assert row.max_length_m is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("arc_id,structure_id,source\na,s,x\n", "structure_type"),
        ("arc_id,structure_id,structure_type,source\na,s,canal,x\n", "must be one of"),
        ("arc_id,structure_id,structure_type,is_open,source\na,s,lock,maybe,x\n", "true or false"),
        ("arc_id,structure_id,structure_type,max_beam_m,source\na,s,lock,wide,x\n", "non-numeric"),
        ("arc_id,structure_id,structure_type,max_beam_m,source\na,s,lock,-1,x\n", "greater than"),
    ],
)
def test_restriction_loader_rejects_bad_rows(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        load_arc_restrictions_csv(write(tmp_path, text))


def test_vessel_loader_defaults_and_duplicates(tmp_path: Path) -> None:
    (row,) = load_navigability_vessels_csv(
        write(tmp_path, "vessel_id,length_m,beam_m,draught_m\nv,80,9.5,2.5\n")
    )
    assert row.air_draught_m is None and not row.carries_dangerous_goods
    assert row.source == "unspecified"
    with pytest.raises(ValidationError, match="Duplicate vessel_id"):
        load_navigability_vessels_csv(
            write(tmp_path, "vessel_id,length_m,beam_m,draught_m\nv,80,9,2\nv,81,9,2\n")
        )
    with pytest.raises(ValidationError, match="missing required columns"):
        load_navigability_vessels_csv(write(tmp_path, "vessel_id,length_m\nv,80\n"))
