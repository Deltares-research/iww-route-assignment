"""Trip trace and the five check tables, against hand calculations on the synthetic run.

Rates: 30 EUR/h (labour 10, maintenance 2, insurance 1, interest 4 x 0.5, depreciation 4,
port 5, other 6) and 10 EUR/km. Fuel 2 EUR/l, idle factor 0.1. M1 on class I burns 4 l/km
empty, 6 l/km half loaded and 8 l/km full.

Trip a (175 t of 350 t) by cost, route A-B-D:
- A-B: 10 km at 10 km/h = 1 h. 100 + 30 + 60 l x 2 = 250.
- B-D: 10 km, 1 h sailing + 0.5 h lock = 1.5 h. Fuel 60 + 6 x 10 x 0.1 x 0.5 = 63 l.
  100 + 45 + 126 = 271.
Sections 521, fixed cost 5 EUR/t x 175 t + 7 EUR/h x 2 h = 889, total 1410.
By time, route A-C-D: 300 + 30 x 1.5 + 6 l/km x 30 km x 2 = 705, plus 889 = 1594.
"""

from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from iww_route_assignment import AssignmentRun, ValidationError, run_assignment, trace_trip
from iww_route_assignment.assignment import TRACE_COLUMNS
from iww_route_assignment.io import largest_hand_difference_eur, module_check_tables
from iww_route_assignment.io.checks import (
    HAND_CHECK_TOLERANCE_EUR,
    assignment_check,
    dimension_control_check,
    network_check,
)

from .conftest import WorkflowFiles, workflow_config, workflow_graph, workflow_paths

TRIP_A = "ivs_trips_test:2026:a"
TRIP_B = "ivs_trips_test:2026:b"
TRIP_D = "ivs_trips_test:2026:d"


@pytest.fixture
def by_cost(workflow_files: WorkflowFiles) -> AssignmentRun:
    return run_assignment(workflow_paths(workflow_files), workflow_config())


@pytest.fixture
def by_time(workflow_files: WorkflowFiles) -> AssignmentRun:
    return run_assignment(workflow_paths(workflow_files), workflow_config("travel_time"))


@pytest.fixture
def cost_rates(workflow_files: WorkflowFiles) -> pd.DataFrame:
    return pd.read_csv(workflow_files.reference / "cost_rates.csv")


def test_trace_lists_each_section_with_parts_that_add_up(by_cost: AssignmentRun) -> None:
    trace = trace_trip(by_cost.result, TRIP_A)
    assert tuple(trace.columns) == TRACE_COLUMNS
    assert trace["seq"].tolist() == [0, 1]
    assert trace["arc_id"].tolist() == ["A|B", "B|D"]
    assert trace["section_code"].tolist() == ["NL01", "NL02"]
    assert trace["cemt_class"].tolist() == ["I", "I"]
    assert trace["from_node"].tolist() == ["A", "B"]
    assert trace["distance_km"].tolist() == [10.0, 10.0]
    assert trace["speed_km_per_h"].tolist() == [10.0, 10.0]
    assert trace["lock_count"].tolist() == [0, 1]
    assert trace["lock_time_h"].tolist() == [0.0, 0.5]
    assert trace["reported_time_h"].tolist() == pytest.approx([1.0, 1.5])
    assert trace["fuel_rate_l_per_km"].tolist() == pytest.approx([6.0, 6.0])
    assert trace["fuel_l"].tolist() == pytest.approx([60.0, 63.0])
    assert trace["distance_cost_eur"].tolist() == pytest.approx([100.0, 100.0])
    assert trace["time_cost_eur"].tolist() == pytest.approx([30.0, 45.0])
    assert trace["fuel_cost_eur"].tolist() == pytest.approx([120.0, 126.0])
    assert trace["reported_cost_eur"].tolist() == pytest.approx([250.0, 271.0])
    assert trace["cumulative_km"].tolist() == [10.0, 20.0]
    assert trace["cumulative_reported_cost_eur"].tolist() == pytest.approx([250.0, 521.0])
    parts = trace[["distance_cost_eur", "time_cost_eur", "fuel_cost_eur"]].sum(axis=1)
    np.testing.assert_allclose(parts, trace["reported_cost_eur"])
    trip = by_cost.result.trips.set_index("trip_id").loc[TRIP_A]
    assert trace["reported_cost_eur"].sum() + trip["fixed_cost_eur"] == pytest.approx(
        trip["reported_total_cost_eur"]
    )
    assert trace["fuel_l"].sum() == pytest.approx(trip["fuel_l"])


def test_trace_explains_why_a_trip_has_no_trace(by_cost: AssignmentRun) -> None:
    with pytest.raises(ValidationError, match="it was not routed"):
        trace_trip(by_cost.result, TRIP_D)
    with pytest.raises(ValidationError, match="no such trip"):
        trace_trip(by_cost.result, "ivs_trips_test:2026:zzz")


def test_five_tables_in_workflow_order(
    by_cost: AssignmentRun, by_time: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    tables = module_check_tables(by_cost, cost_rates, other=by_time.result)
    assert list(tables) == ["1_trips", "2_network", "3_dimension_control", "4_cost", "5_assignment"]
    assert largest_hand_difference_eur(tables) <= HAND_CHECK_TOLERANCE_EUR


def test_trips_table_shows_every_source_row_and_its_fate(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    trips = module_check_tables(by_cost, cost_rates)["1_trips"].set_index("Pseudo_ReisID")
    assert trips.index.tolist() == ["a", "b", "c", "d", "e", "f", "g", "h"]
    assert trips["status"].to_dict() == {
        "a": "success",
        "b": "success",
        "c": "success",
        "d": "vessel_fail",
        "e": "excluded",
        "f": "rejected",
        "g": "cost_fail",
        "h": "success",
    }
    assert "repeated trip id" in trips.loc["e", "detail"]
    assert "non-numeric" in trips.loc["f", "detail"]
    row = trips.loc["a"]
    assert (row["source_SK_CODE"], row["vessel_rws_class"]) == ("M1", "M1")
    assert (row["source_Lading"], row["cargo_t"]) == ("175", 175.0)
    assert (row["source_Laadvermogen"], row["loading_ratio"]) == ("350", 0.5)
    assert row["source_Eerste_Tijdstip"] == row["departure_time"] == "2026-03-01 08:00:00"
    assert (row["source_Segment"], row["appearance"]) == ("Droge bulkvaart", "droge_bulk")
    assert (row["origin_node_id"], row["destination_node_id"]) == ("A", "D")
    # Segment "Overig" with loading code 1 is an empty trip priced as "overig".
    assert (trips.loc["b", "appearance"], trips.loc["b", "cargo_t"]) == ("overig", 0.0)
    assert trips.loc["d", "source_vessel_class"] == "B04"


def test_network_table_shows_attributes_and_limits(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    network = module_check_tables(by_cost, cost_rates)["2_network"]
    assert network["arc_id"].tolist() == ["A|B", "B|D", "B|D"]  # only used sections
    lock = network[network["structure_type"] == "lock"].iloc[0]
    assert (lock["structure_id"], lock["section_code"]) == ("NLLOCK1", "NL02")
    assert (lock["length_km"], lock["speed_km_per_h"]) == (10.0, 10.0)
    assert (lock["lock_count"], lock["lock_time_h"], lock["trip_count"]) == (1, 0.5, 4)
    # Chamber: 12 m wide, 100 m long, sill at 200 cm.
    assert (lock["max_beam_m"], lock["max_length_m"], lock["max_draught_m"]) == (12.0, 100.0, 2.0)
    fairway = network[network["structure_type"] == "waterway"].iloc[0]
    # mwidthcm 1000 and mdraughtcm 300 on the graph edge.
    assert (fairway["max_beam_m"], fairway["max_draught_m"]) == (10.0, 3.0)
    assert fairway["limit_source"].startswith("euris:fairway")


def test_dimension_control_reports_unmet_limits_with_their_penalty(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    control = module_check_tables(by_cost, cost_rates)["3_dimension_control"]
    # Loaded M1 draws 2.2 m against the 2.0 m sill. The empty trip b fits.
    assert control["trip_id"].str[-1].tolist() == ["a", "c", "h"]
    assert set(control["arc_id"]) == {"B|D"}
    assert set(control["restriction"]) == {"draught"}
    assert set(control["status"]) == {"not_navigable"}
    assert set(control["structure_id"]) == {"NLLOCK1"}
    assert control["ship_value_plus_margin_m"].tolist() == [2.2, 2.2, 2.2]
    assert control["limit_m"].tolist() == [2.0, 2.0, 2.0]
    # Draught is reported but does not steer by default: 0 penalty hours.
    assert control["penalty_h_for_this_type_on_section"].tolist() == [0.0, 0.0, 0.0]


def test_cost_table_recomputes_each_section_from_the_raw_rate_row(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    cost = module_check_tables(by_cost, cost_rates)["4_cost"]
    assert len(cost) == 8  # four routed trips, two sections each
    row = cost[(cost["trip_id"] == TRIP_A) & (cost["arc_id"] == "B|D")].iloc[0]
    assert row["cost_rate_row"] == "droge_bulk/M1"
    assert row["hourly_rate_eur_per_h"] == pytest.approx(30.0)
    assert row["hand_time_h"] == pytest.approx(1.5)
    assert row["hand_fuel_l"] == pytest.approx(63.0)
    assert row["hand_distance_cost_eur"] == pytest.approx(100.0)
    assert row["hand_time_cost_eur"] == pytest.approx(45.0)
    assert row["hand_fuel_cost_eur"] == pytest.approx(126.0)
    assert row["hand_reported_cost_eur"] == pytest.approx(271.0)
    for name in ("time_h", "fuel_l", "distance_cost_eur", "time_cost_eur", "fuel_cost_eur"):
        library = "reported_time_h" if name == "time_h" else name
        np.testing.assert_allclose(cost[library], cost[f"hand_{name}"], rtol=0, atol=1e-9)
    assert cost["difference_eur"].abs().max() <= HAND_CHECK_TOLERANCE_EUR
    empty = cost[cost["trip_id"] == TRIP_B]
    assert empty["cost_rate_row"].tolist() == ["overig/M1", "overig/M1"]


def test_cost_table_does_not_depend_on_rate_row_order_or_label_case(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    reordered = cost_rates.iloc[::-1].assign(rws_class="m1").reset_index(drop=True)
    first = module_check_tables(by_cost, cost_rates)["4_cost"]
    second = module_check_tables(by_cost, reordered)["4_cost"]
    pd.testing.assert_frame_equal(
        first.drop(columns="cost_rate_row"), second.drop(columns="cost_rate_row")
    )


def test_assignment_table_adds_fixed_cost_once_and_compares_metrics(
    by_cost: AssignmentRun, by_time: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    table = assignment_check(by_cost, cost_rates, by_time.result).set_index("trip_id")
    a = table.loc[TRIP_A]
    assert a["hand_fixed_cost_eur"] == pytest.approx(889.0)
    assert a["hand_reported_total_cost_eur"] == pytest.approx(1410.0)
    assert a["reported_total_cost_eur"] == pytest.approx(1410.0)
    assert a["distance_cost_eur"] + a["time_cost_eur"] + a["fuel_cost_eur"] == pytest.approx(521.0)
    assert (a["lock_passages"], a["n_edges"]) == (1, 2)
    assert a["route"] == "A|B > B|D"
    assert a["route__travel_time"] == "A|C > C|D"
    assert not a["same_route_as_travel_time"]
    assert a["reported_time_h__travel_time"] == pytest.approx(1.5)
    assert a["distance_km__travel_time"] == pytest.approx(30.0)
    assert a["reported_total_cost_eur__travel_time"] == pytest.approx(1594.0)
    # An empty trip has no fixed cost, but it has operating cost: 200 + 75 + 82 l x 2 = 439.
    assert table.loc[TRIP_B, "hand_fixed_cost_eur"] == 0.0
    assert table.loc[TRIP_B, "hand_reported_total_cost_eur"] == pytest.approx(439.0)
    assert np.isnan(table.loc[TRIP_D, "hand_fixed_cost_eur"])
    assert np.isnan(table.loc[TRIP_D, "difference_eur"])
    assert table["difference_eur"].abs().max() <= HAND_CHECK_TOLERANCE_EUR
    alone = assignment_check(by_cost, cost_rates)
    assert not any("__" in column or column.startswith("same_route") for column in alone.columns)
    same = assignment_check(by_cost, cost_rates, by_cost.result).set_index("trip_id")
    assert same.loc[TRIP_A, "same_route_as_travel_cost_eur"]


def test_largest_difference_reads_both_cost_tables(
    by_cost: AssignmentRun, cost_rates: pd.DataFrame
) -> None:
    tables = dict(module_check_tables(by_cost, cost_rates))
    tables["4_cost"] = tables["4_cost"].assign(difference_eur=[0.0] * 7 + [-0.5])
    assert largest_hand_difference_eur(tables) == 0.5
    tables["5_assignment"] = tables["5_assignment"].assign(difference_eur=2.0)
    assert largest_hand_difference_eur(tables) == 2.0
    nothing = {name: tables[name].iloc[0:0] for name in ("4_cost", "5_assignment")}
    assert largest_hand_difference_eur(nothing) == 0.0


def test_tables_without_any_limit(tmp_path: Path, workflow_files: WorkflowFiles) -> None:
    graph = workflow_graph()
    for _, _, data in graph.edges(data=True):
        del data["mwidthcm"], data["mdraughtcm"]
    plain = tmp_path / "plain_graph.pickle"
    plain.write_bytes(pickle.dumps(graph))
    paths = replace(workflow_paths(workflow_files), graph=plain, structures=None)
    run = run_assignment(paths, workflow_config())
    assert run.inputs.restrictions == ()
    network = network_check(run)
    assert network["arc_id"].tolist() == ["A|B", "B|D"]
    assert "structure_id" not in network.columns
    control = dimension_control_check(run)
    assert control.empty and "limit_m" in control.columns
