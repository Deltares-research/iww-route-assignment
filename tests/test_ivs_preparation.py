"""Tests for the IVS trip adapter and the pure preparation service."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import (
    AppearanceRule,
    ArcCostRequest,
    ArcProfile,
    ArcTravelInput,
    CostRates,
    RouteSequence,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    TripCostInput,
    TripObservation,
    TripRejectionReason,
    ValidationError,
    build_route_index,
    build_vessel_type_index,
    calculate_arc_cost,
    calculate_trip_fixed_cost,
    summarise_trip_conversion,
    trips_to_arc_requests,
)
from iww_route_assignment.io import (
    ARC_REQUEST_COLUMNS,
    arc_requests_to_frame,
    ivs_trips_from_frame,
    load_appearance_map_csv,
    load_arc_profiles_csv,
    load_ivs_trips_csv,
    load_route_sequences_csv,
    load_vessel_type_map_csv,
    trip_rejections_to_frame,
)
from iww_route_assignment.preparation import (
    DEFAULT_NSTR_APPEARANCE,
    appearance_for_trip,
    loading_ratio_for_trip,
)

FIXTURES = Path(__file__).parent / "fixtures"

IVS_FIXTURE = FIXTURES / "ivs_trips.csv"
ROUTE_FIXTURE = FIXTURES / "arc_profiles.csv"
VESSEL_TYPE_FIXTURE = FIXTURES / "vessel_type_map.csv"
APPEARANCE_FIXTURE = FIXTURES / "nstr_appearance_map.csv"


@pytest.fixture
def trips() -> tuple[TripObservation, ...]:
    return load_ivs_trips_csv(IVS_FIXTURE, source_dataset="fixture")


@pytest.fixture
def routes() -> dict[str, RouteSequence]:
    return build_route_index(
        load_route_sequences_csv(
            FIXTURES / "route_sequences.csv", load_arc_profiles_csv(ROUTE_FIXTURE)
        )
    )


@pytest.fixture
def vessel_types() -> dict[int, str]:
    return load_vessel_type_map_csv(VESSEL_TYPE_FIXTURE)


def _trip(**overrides: object) -> TripObservation:
    values: dict[str, object] = {
        "source_row_id": "fixture:0",
        "origin_locode": "NLAAA",
        "destination_locode": "NLBBB",
        "vessel_type_id": 2,
        "capacity_t": 350.0,
        "cargo_t": 175.0,
        "containers_teu": 0.0,
        "cargo_code": "2",
        "source_dataset": "fixture",
    }
    values.update(overrides)
    return TripObservation(**values)  # type: ignore[arg-type]


def test_loads_every_fixture_row_with_deterministic_identifiers(
    trips: tuple[TripObservation, ...],
) -> None:
    assert len(trips) == 9
    assert [trip.source_row_id for trip in trips] == [f"fixture:{index}" for index in range(9)]
    assert trips[0].origin_locode == "NLAAA"
    assert trips[0].vessel_type_id == 2
    assert trips[0].observed_on == "2023-01-02"
    assert trips[0].loading_code == "7"


def test_transported_weight_is_converted_from_kilogrammes(
    trips: tuple[TripObservation, ...],
) -> None:
    assert trips[0].cargo_t == pytest.approx(175.0)
    assert trips[0].capacity_t == pytest.approx(350.0)


def test_empty_loading_code_turns_a_missing_weight_into_zero_cargo(
    trips: tuple[TripObservation, ...],
) -> None:
    assert trips[1].loading_code == "1"
    assert trips[1].cargo_t == 0.0


def test_missing_weight_without_the_empty_code_stays_unknown(
    trips: tuple[TripObservation, ...],
) -> None:
    assert trips[7].loading_code == "7"
    assert trips[7].cargo_t is None


def test_numeric_cargo_codes_are_read_without_a_decimal_part(
    trips: tuple[TripObservation, ...],
) -> None:
    assert trips[0].cargo_code == "2"
    assert trips[1].cargo_code == ""


def test_missing_required_column_is_rejected() -> None:
    frame = pd.read_csv(IVS_FIXTURE).drop(columns=["unlo_bestemming"])
    with pytest.raises(ValidationError, match="unlo_bestemming"):
        ivs_trips_from_frame(frame, source_dataset="fixture")


def test_empty_export_is_rejected() -> None:
    frame = pd.read_csv(IVS_FIXTURE).iloc[0:0]
    with pytest.raises(ValidationError, match="no data rows"):
        ivs_trips_from_frame(frame, source_dataset="fixture")


def test_empty_source_dataset_is_rejected() -> None:
    frame = pd.read_csv(IVS_FIXTURE)
    with pytest.raises(ValidationError, match="source_dataset"):
        ivs_trips_from_frame(frame, source_dataset="  ")


def test_missing_value_in_required_column_is_rejected() -> None:
    frame = pd.read_csv(IVS_FIXTURE)
    frame.loc[0, "unlo_herkomst"] = None
    with pytest.raises(ValidationError, match="unlo_herkomst"):
        ivs_trips_from_frame(frame, source_dataset="fixture")


def test_non_integer_vessel_type_is_rejected() -> None:
    frame = pd.read_csv(IVS_FIXTURE).astype({"v15_1_scheepstype_rws": "float64"})
    frame.loc[0, "v15_1_scheepstype_rws"] = 2.5
    with pytest.raises(ValidationError, match="non-integer"):
        ivs_trips_from_frame(frame, source_dataset="fixture")


def test_negative_cargo_weight_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cargo_t"):
        _trip(cargo_t=-1.0)


def test_non_finite_capacity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="capacity_t"):
        _trip(capacity_t=float("inf"))


def test_boolean_vessel_type_is_rejected() -> None:
    with pytest.raises(ValidationError, match="vessel_type_id"):
        _trip(vessel_type_id=True)


def test_loading_ratio_uses_cargo_over_capacity() -> None:
    ratio, source, notes = loading_ratio_for_trip(_trip(cargo_t=175.0, capacity_t=350.0))
    assert ratio == pytest.approx(0.5)
    assert source == "derived:cargo_t/capacity_t"
    assert notes == ()


def test_loading_ratio_above_one_is_clipped_and_noted() -> None:
    ratio, _, notes = loading_ratio_for_trip(_trip(cargo_t=400.0, capacity_t=350.0))
    assert ratio == 1.0
    assert notes == ("loading_ratio_clipped_from_1.1429",)


def test_zero_capacity_is_rejected_by_the_ratio_rule() -> None:
    with pytest.raises(ValidationError, match="capacity_t"):
        loading_ratio_for_trip(_trip(capacity_t=0.0))


def test_unknown_cargo_weight_is_rejected_by_the_ratio_rule() -> None:
    with pytest.raises(ValidationError, match="cargo_t is missing"):
        loading_ratio_for_trip(_trip(cargo_t=None))


def test_containers_decide_the_appearance_before_the_cargo_code() -> None:
    appearance, source = appearance_for_trip(
        _trip(containers_teu=4.0, cargo_code="2"), DEFAULT_NSTR_APPEARANCE
    )
    assert appearance == "container"
    assert source == "observed:containers_teu"


def test_empty_trip_without_a_cargo_code_uses_the_documented_assumption() -> None:
    appearance, source = appearance_for_trip(
        _trip(cargo_t=0.0, cargo_code=""), DEFAULT_NSTR_APPEARANCE
    )
    assert appearance == "overig"
    assert source == "assumption:empty-trip-appearance-v1"


def test_loaded_trip_without_a_cargo_code_raises() -> None:
    with pytest.raises(ValidationError, match="cargo_code"):
        appearance_for_trip(_trip(cargo_t=100.0, cargo_code=""), DEFAULT_NSTR_APPEARANCE)


def test_appearance_source_reports_the_mapping_source() -> None:
    mapping = {"2": AppearanceRule(appearance="droge_bulk", source="reference:bivas")}
    appearance, source = appearance_for_trip(_trip(cargo_code="2"), mapping)
    assert appearance == "droge_bulk"
    assert source == "reference:bivas:chapter=2"


def test_unmapped_cargo_code_raises() -> None:
    with pytest.raises(ValidationError, match="XX"):
        appearance_for_trip(_trip(cargo_code="XX"), DEFAULT_NSTR_APPEARANCE)


def test_conversion_accepts_and_rejects_the_expected_rows(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    assert [request.source_row_id for request in result.requests] == [
        "fixture:0",
        "fixture:1",
        "fixture:2",
        "fixture:3",
    ]
    assert [(rejection.source_row_id, rejection.reason) for rejection in result.rejections] == [
        ("fixture:4", TripRejectionReason.UNKNOWN_VESSEL_TYPE),
        ("fixture:5", TripRejectionReason.MISSING_ARC_SEQUENCE),
        ("fixture:6", TripRejectionReason.NON_POSITIVE_CAPACITY),
        ("fixture:7", TripRejectionReason.MISSING_CARGO_WEIGHT),
        ("fixture:8", TripRejectionReason.UNKNOWN_APPEARANCE),
    ]


def test_first_request_matches_a_hand_calculation(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    request = result.requests[0]
    assert request.arc_id == "ab"
    assert request.vessel_rws_class == "M1"
    assert request.waterway_cemt_class == "Va"
    assert request.appearance == "droge_bulk"
    assert request.loading_ratio == pytest.approx(175.0 / 350.0)
    assert request.distance_km == pytest.approx(50.0)
    assert request.realised_speed_km_per_h == pytest.approx(12.0)
    assert request.is_loaded is True
    assert request.route_source == "synthetic-test;arc:synthetic-test"
    assert request.vessel_class_source == "observed:SK_CODE/sk_code"


def test_empty_trip_is_marked_unloaded(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    empty = result.requests[1]
    assert empty.cargo_t == 0.0
    assert empty.is_loaded is False
    assert empty.loading_ratio == 0.0
    assert empty.appearance == "overig"
    assert empty.appearance_source == "assumption:empty-trip-appearance-v1"


def test_container_trip_without_cargo_weight_stays_loaded(
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(
        (_trip(cargo_t=0.0, containers_teu=6.0),),
        routes=routes,
        vessel_types=vessel_types,
    )
    request = result.requests[0]
    assert request.is_loaded is True
    assert request.appearance == "container"


def test_conversion_is_independent_of_input_order(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    forward = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    reversed_result = trips_to_arc_requests(
        tuple(reversed(trips)), routes=routes, vessel_types=vessel_types
    )
    assert sorted(forward.requests, key=lambda item: item.case_row_id) == sorted(
        reversed_result.requests, key=lambda item: item.case_row_id
    )
    assert sorted(forward.rejections, key=lambda item: item.source_row_id) == sorted(
        reversed_result.rejections, key=lambda item: item.source_row_id
    )


def test_duplicate_source_row_identifiers_are_rejected(
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    with pytest.raises(ValidationError, match="Duplicate source_row_id"):
        trips_to_arc_requests((_trip(), _trip()), routes=routes, vessel_types=vessel_types)


def test_duplicate_route_pair_is_rejected() -> None:
    profile = RouteSequence(
        "r",
        "NLAAA",
        "NLBBB",
        (ArcProfile("ab", "a", "b", 50.0, "Va", 12.0, "synthetic-test"),),
        "synthetic-test",
    )
    with pytest.raises(ValidationError, match="Duplicate route_id"):
        build_route_index((profile, profile))


def test_empty_route_table_is_rejected() -> None:
    with pytest.raises(ValidationError, match="At least one route sequence"):
        build_route_index(())


def test_conflicting_vessel_type_mapping_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Conflicting vessel classes"):
        build_vessel_type_index(((2, "M1"), (2, "M2")))


def test_repeated_identical_vessel_type_mapping_is_accepted() -> None:
    assert build_vessel_type_index(((2, "M1"), (2, "M1"))) == {2: "M1"}


def test_unsupported_waterway_class_is_rejected() -> None:
    with pytest.raises(ValidationError, match="CEMT"):
        ArcProfile("ab", "a", "b", 50.0, "IX", 12.0, "synthetic-test")


def test_zero_distance_structure_is_allowed() -> None:
    assert ArcProfile("aa", "a", "a", 0.0, "Va", 12.0, "synthetic-test").distance_km == 0


def test_appearance_map_requires_single_digit_chapters() -> None:
    with pytest.raises(ValidationError, match="missing required columns"):
        load_appearance_map_csv(ROUTE_FIXTURE)


def test_appearance_map_loads_with_row_sources() -> None:
    mapping = load_appearance_map_csv(APPEARANCE_FIXTURE)
    assert mapping["9"] == AppearanceRule(appearance="natte_bulk", source="synthetic-test")


def test_appearance_map_rejects_multi_digit_chapters(tmp_path: Path) -> None:
    path = tmp_path / "appearance.csv"
    path.write_text("nstr_chapter,appearance,source\n21,droge_bulk,test\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="nstr_chapter"):
        load_appearance_map_csv(path)


def test_appearance_map_rejects_conflicting_chapters(tmp_path: Path) -> None:
    path = tmp_path / "appearance.csv"
    path.write_text(
        "nstr_chapter,appearance,source\n2,droge_bulk,test\n2,natte_bulk,test\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Conflicting appearance rule"):
        load_appearance_map_csv(path)


def test_appearance_map_overrides_the_built_in_assumption(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    mapping = load_appearance_map_csv(APPEARANCE_FIXTURE)
    default = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    overridden = trips_to_arc_requests(
        trips, routes=routes, vessel_types=vessel_types, appearance_map=mapping
    )
    assert default.requests[0].appearance_source.startswith("assumption:")
    assert overridden.requests[0].appearance_source == "synthetic-test:chapter=2"


def test_vessel_type_map_requires_its_columns() -> None:
    with pytest.raises(ValidationError, match="missing required columns"):
        load_vessel_type_map_csv(ROUTE_FIXTURE)


def test_vessel_type_map_rejects_conflicting_rows(tmp_path: Path) -> None:
    path = tmp_path / "vessel_types.csv"
    path.write_text("ship_type_id,rws_class\n2,M1\n2,M2\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Conflicting vessel classes"):
        load_vessel_type_map_csv(path)


def test_summary_reports_coverage_and_assumptions(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    summary = summarise_trip_conversion(result)
    assert summary["source_rows"] == 9
    assert summary["accepted_rows"] == 4
    assert summary["accepted_share"] == pytest.approx(4 / 9)
    assert summary["rejection_reason_counts"] == {
        "missing_cargo_weight": 1,
        "missing_arc_sequence": 1,
        "non_positive_capacity": 1,
        "unknown_appearance": 1,
        "unknown_vessel_type": 1,
    }
    assert summary["appearance_counts"] == {"container": 1, "droge_bulk": 2, "overig": 1}
    assert summary["assumed_appearance_rows"] == 3
    assert summary["assumed_appearance_share"] == pytest.approx(0.75)
    assert summary["clipped_loading_ratio_rows"] == 1


def test_request_frame_starts_with_the_runner_columns(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    frame = arc_requests_to_frame(result.requests)
    assert tuple(frame.columns[: len(ARC_REQUEST_COLUMNS)]) == ARC_REQUEST_COLUMNS
    assert "route_source" in frame.columns
    assert len(frame) == 4


def test_request_frame_requires_at_least_one_row() -> None:
    with pytest.raises(ValidationError, match="No arc-cost requests"):
        arc_requests_to_frame(())


def test_rejection_frame_keeps_its_columns_when_empty() -> None:
    frame = trip_rejections_to_frame(())
    assert list(frame.columns) == ["source_row_id", "reason", "detail"]
    assert frame.empty


def test_requests_feed_the_cost_calculator(
    trips: tuple[TripObservation, ...],
    routes: dict[str, RouteSequence],
    vessel_types: dict[int, str],
    cost_rates: CostRates,
    scenario: ScenarioCostParameters,
    fuel_provider: TableFuelConsumptionProvider,
) -> None:
    result = trips_to_arc_requests(trips, routes=routes, vessel_types=vessel_types)
    request: ArcCostRequest = result.requests[0]
    fuel = fuel_provider.resolve(
        request.vessel_rws_class,
        request.waterway_cemt_class,
        loading_ratio=request.loading_ratio,
    )
    arc = calculate_arc_cost(
        ArcTravelInput(
            distance_km=request.distance_km,
            realised_speed_km_per_h=request.realised_speed_km_per_h,
        ),
        cost_rates,
        scenario,
        fuel,
    )
    fixed = calculate_trip_fixed_cost(
        TripCostInput(cargo_t=request.cargo_t, is_loaded=request.is_loaded),
        cost_rates,
        scenario,
    )
    assert arc.movement_time_h == pytest.approx(50.0 / 12.0)
    assert fixed.total_fixed_cost_eur > 0.0
