"""End-to-end assignment on the synthetic network, checked against hand calculations.

Rates: hourly 30 EUR/h (labour 10, maintenance 2, insurance 1, interest 4 x 0.5,
depreciation 4, port 5, other 6), 10 EUR/km. Fuel price 2 EUR/l. M1 on class I burns
40 l/h empty and 60 l/h half loaded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from iww_route_assignment import (
    ArcRestriction,
    AssignmentSettings,
    MissingCostPolicy,
    NavigabilityPenalties,
    RouteCostMetric,
    StructureType,
    ValidationError,
    VoyageStatus,
    assign_traffic,
)

from .conftest import make_network, make_rates, make_trip, make_trips

COST = AssignmentSettings(metric=RouteCostMetric.TRAVEL_COST_EUR)
TIME = AssignmentSettings(metric=RouteCostMetric.TRAVEL_TIME)


def _route(result, voyage_index: int = 0) -> list[str]:
    edges = result.voyage_edges
    return edges.loc[edges["voyage_index"] == voyage_index, "arc_id"].tolist()


def test_time_and_cost_metrics_choose_different_routes(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip("empty", 0.0)])
    by_time = assign_traffic(trips, network, cost_inputs, catalogue, settings=TIME)
    by_cost = assign_traffic(trips, network, cost_inputs, catalogue, settings=COST)
    assert _route(by_time) == ["A|C", "C|D"]
    assert _route(by_cost) == ["A|B", "B|D"]
    assert by_time.voyages.loc[0, "route_weight"] == pytest.approx(1.5)
    # Route 1 by cost: 200 distance + 30 x 2 h + 40 l/h x 2 h x 2 EUR/l = 420.
    assert by_cost.voyages.loc[0, "route_weight"] == pytest.approx(420.0)


def test_reported_cost_adds_fixed_cost_once(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip("empty", 0.0), make_trip("half", 175.0)])
    voyages = assign_traffic(trips, network, cost_inputs, catalogue, settings=COST).voyages
    empty, half = voyages.iloc[0], voyages.iloc[1]
    assert empty["fixed_cost_eur"] == 0.0
    assert empty["reported_total_cost_eur"] == pytest.approx(420.0)
    # Half load: 200 + 60 + 60 l/h x 2 h x 2 = 500 on arcs.
    # Fixed: 2 x 175 + 3 x 175 + 7 EUR/h x 2 h = 889, added once.
    assert half["fixed_cost_eur"] == pytest.approx(889.0)
    assert half["reported_total_cost_eur"] == pytest.approx(1389.0)
    assert half["total_cost_eur"] == half["choice_total_cost_eur"]
    assert half["fuel_l"] == pytest.approx(120.0)
    assert half["reported_time_h"] == pytest.approx(2.0)


def _beam_limit(structure_id: str, beam_m: float) -> ArcRestriction:
    return ArcRestriction(
        "B|D", structure_id, StructureType.LOCK, "synthetic-test", max_beam_m=beam_m
    )


def test_penalty_steers_choice_but_not_reported_cost(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip("empty", 0.0)])
    result = assign_traffic(
        trips, network, cost_inputs, catalogue, [_beam_limit("NLLOCK1", 4.0)], settings=COST
    )
    voyage = result.voyages.iloc[0]
    assert _route(result) == ["A|C", "C|D"]
    assert voyage["penalty_total_h"] == 0.0
    assert voyage["reported_total_cost_eur"] == voyage["choice_total_cost_eur"]
    assert voyage["penalised_section_count"] == 0


def test_penalty_on_only_route_is_reported(cost_inputs, catalogue) -> None:
    network = make_network()
    restrictions = [
        _beam_limit("NLLOCK1", 4.0),
        ArcRestriction("C|D", "DELOCK2", StructureType.LOCK, "synthetic-test", max_beam_m=4.55),
    ]
    result = assign_traffic(
        make_trips([make_trip("empty", 0.0)]),
        network,
        cost_inputs,
        catalogue,
        restrictions,
        settings=TIME,
    )
    voyage = result.voyages.iloc[0]
    # Abroad: 5 h per metre over. M1 beam 5.05 m against 4.55 m is 2.5 h; Dutch is 1000 h.
    assert _route(result) == ["A|C", "C|D"]
    assert voyage["penalty_total_h"] == pytest.approx(2.5)
    assert voyage["choice_time_h"] == pytest.approx(1.5 + 2.5)
    assert voyage["reported_time_h"] == pytest.approx(1.5)
    assert voyage["penalised_section_count"] == 1
    penalised = result.penalised.set_index("structure_id")
    assert penalised.loc["DELOCK2", "penalty_h"] == pytest.approx(2.5)
    assert penalised.loc["DELOCK2", "trips"] == 1


def test_zero_penalty_switches_a_check_off(network, cost_inputs, catalogue) -> None:
    penalties = NavigabilityPenalties(beam_h=0.0)
    result = assign_traffic(
        make_trips([make_trip("empty", 0.0)]),
        network,
        cost_inputs,
        catalogue,
        [_beam_limit("NLLOCK1", 4.0)],
        settings=COST,
        penalties=penalties,
    )
    assert _route(result) == ["A|B", "B|D"]
    assert result.voyages.loc[0, "penalised_section_count"] == 1


def test_draught_limit_uses_loaded_draught(network, cost_inputs, catalogue) -> None:
    shallow = ArcRestriction(
        "B|D", "NLBD", StructureType.WATERWAY, "synthetic-test", max_draught_m=2.0
    )
    trips = make_trips([make_trip("empty", 0.0), make_trip("half", 175.0)])
    steer = NavigabilityPenalties(draught_h=1000.0)
    result = assign_traffic(
        trips, network, cost_inputs, catalogue, [shallow], settings=COST, penalties=steer
    )
    default = assign_traffic(trips, network, cost_inputs, catalogue, [shallow], settings=COST)
    # M1 draught: empty 1.0 m fits 2.0 m; loaded 2.2 m does not and avoids B-D.
    assert _route(result, 0) == ["A|B", "B|D"]
    assert _route(result, 1) == ["A|C", "C|D"]
    # By default the draught check reports but does not steer.
    assert _route(default, 1) == ["A|B", "B|D"]
    assert default.voyages.loc[1, "penalised_section_count"] == 1


def test_routing_ratio_step_changes_search_keys_only(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip("a", 170.0), make_trip("b", 180.0)])
    coarse = assign_traffic(
        trips,
        network,
        cost_inputs,
        catalogue,
        settings=AssignmentSettings(routing_ratio_step=0.5),
    )
    exact = assign_traffic(
        trips,
        network,
        cost_inputs,
        catalogue,
        settings=AssignmentSettings(routing_ratio_step=None),
    )
    assert coarse.voyages["routing_loading_ratio"].tolist() == [0.5, 0.5]
    for column in ("reported_total_cost_eur", "fuel_l", "loading_ratio"):
        np.testing.assert_allclose(coarse.voyages[column], exact.voyages[column])


def test_result_does_not_depend_on_trip_order(network, cost_inputs, catalogue) -> None:
    trips = [make_trip(f"t{i}", cargo) for i, cargo in enumerate((0.0, 100.0, 175.0, 350.0))]
    forward = assign_traffic(make_trips(trips), network, cost_inputs, catalogue)
    backward = assign_traffic(make_trips(trips[::-1]), network, cost_inputs, catalogue)
    columns = ["trip_id", "reported_total_cost_eur", "choice_total_cost_eur", "n_edges"]
    a = forward.voyages[columns].sort_values("trip_id", ignore_index=True)
    b = backward.voyages[columns].sort_values("trip_id", ignore_index=True)
    pd.testing.assert_frame_equal(a, b)


def test_worker_processes_match_serial(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip(f"t{i}", cargo) for i, cargo in enumerate((0.0, 175.0, 350.0))])
    serial = assign_traffic(trips, network, cost_inputs, catalogue)
    parallel = assign_traffic(
        trips, network, cost_inputs, catalogue, settings=AssignmentSettings(n_workers=2)
    )
    pd.testing.assert_frame_equal(serial.voyages, parallel.voyages)
    pd.testing.assert_frame_equal(serial.voyage_edges, parallel.voyage_edges)


def test_failures_get_a_status_and_a_reason(network, cost_inputs, catalogue) -> None:
    trips = make_trips(
        [
            make_trip("unknown", 0.0, vessel="B04"),
            make_trip("no-rates", 0.0, appearance="container"),
            make_trip("ok", 0.0),
        ]
    )
    result = assign_traffic(trips, network, cost_inputs, catalogue)
    status = result.voyages.set_index("trip_id")["status"]
    assert status["unknown"] == VoyageStatus.VESSEL_FAIL
    assert status["no-rates"] == VoyageStatus.COST_FAIL
    assert status["ok"] == VoyageStatus.SUCCESS
    assert result.coverage.missing_rates == (("M1", "container"),)
    assert result.summary["status_counts"] == {"cost_fail": 1, "success": 1, "vessel_fail": 1}


def test_missing_cost_policy_fail_raises(network, cost_inputs, catalogue) -> None:
    trips = make_trips([make_trip("no-rates", 0.0, appearance="container")])
    with pytest.raises(ValidationError, match="Cost rates missing"):
        assign_traffic(
            trips,
            network,
            cost_inputs,
            catalogue,
            settings=AssignmentSettings(on_missing_cost=MissingCostPolicy.FAIL),
        )


def test_snap_distance_and_disconnected_parts(cost_inputs, catalogue) -> None:
    network = make_network()
    far = assign_traffic(
        make_trips([make_trip("far", 0.0)], origin=(6.0, 52.0)),
        network,
        cost_inputs,
        catalogue,
        settings=AssignmentSettings(max_snap_distance_m=1000.0),
    )
    assert far.voyages.loc[0, "status"] == VoyageStatus.SNAP_FAIL
    same = assign_traffic(
        make_trips([make_trip("same", 0.0)], destination=(4.99, 52.0)),
        network,
        cost_inputs,
        catalogue,
    )
    assert same.voyages.loc[0, "status"] == VoyageStatus.SUCCESS
    assert same.voyages.loc[0, "n_edges"] == 0


def test_class_alias_is_explicit(network, cost_inputs, catalogue) -> None:
    from iww_route_assignment import VesselCatalogue

    aliased = VesselCatalogue(catalogue.references, class_aliases={"M9": "M1"})
    result = assign_traffic(
        make_trips([make_trip("m9", 0.0, vessel="M9")]), network, cost_inputs, aliased
    )
    assert result.voyages.loc[0, "vessel_rws_class"] == "M1"
    assert result.voyages.loc[0, "source_vessel_class"] == "M9"


def test_confirmed_ivs_crosswalk_routes_b0x_labels(network, cost_inputs, catalogue) -> None:
    from iww_route_assignment import TableFuelConsumptionProvider, VesselCatalogue, VesselReference
    from iww_route_assignment.io import IVS_SK_CODE_CROSSWALK, load_fuel_observations_csv

    from .conftest import FIXTURES

    bo4 = VesselReference("BO4", "I", 350.0, 5.05, 38.5, 2.2, 1.0)
    rates = cost_inputs.rates + (make_rates(vessel="BO4"),)
    references = catalogue.references + (bo4,)
    provider = TableFuelConsumptionProvider(
        load_fuel_observations_csv(FIXTURES / "fuel_observations.csv"), references
    )
    costs = type(cost_inputs)(rates, provider, cost_inputs.scenario)
    trips = make_trips([make_trip("b04", 0.0, vessel="B04"), make_trip("b03", 0.0, vessel="B03")])
    plain = VesselCatalogue(catalogue.references + (bo4,))
    mapped = VesselCatalogue(catalogue.references + (bo4,), class_aliases=IVS_SK_CODE_CROSSWALK)
    assert assign_traffic(trips, network, costs, plain).voyages["status"].tolist() == [
        "vessel_fail",
        "vessel_fail",
    ]
    voyages = assign_traffic(trips, network, costs, mapped).voyages
    assert voyages["status"].tolist() == ["success", "vessel_fail"]
    assert voyages.loc[0, "vessel_rws_class"] == "BO4"
    assert dict(IVS_SK_CODE_CROSSWALK) == {"B01": "BO1", "B02": "BO2", "B04": "BO4"}
