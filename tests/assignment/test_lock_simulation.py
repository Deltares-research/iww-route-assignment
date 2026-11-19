"""End-to-end lock queue simulation on small synthetic networks, checked by hand.

Main network: A-B-D (20 km at 10 km/h, 2 h) and A-C-D (30 km at 20 km/h, 1.5 h). A lock with
one 40 x 6 m chamber (a 38.5 x 5.05 m M1 fills it) and a one hour operation time sits half way
along A-C. The fixed passage time of that lock is 0.25 h, so A-C-D (1.75 h) beats A-B-D.
"""

from __future__ import annotations

import dataclasses
import functools
from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import Point

pytest.importorskip("packer2d")
pytest.importorskip("opentnsim")
import opentnsim.core as core  # noqa: E402
import simpy  # noqa: E402

from iww_route_assignment import (  # noqa: E402
    ArcRestriction,
    AssignmentNetwork,
    AssignmentSettings,
    AssignmentTrips,
    NetworkEdge,
    RouteCostMetric,
    StructureType,
    TripObservation,
    ValidationError,
    assign_traffic,
)
from iww_route_assignment.assignment import place_locks  # noqa: E402
from iww_route_assignment.domain import ChamberSpec, LockComplex  # noqa: E402
from iww_route_assignment.io import write_assignment_outputs  # noqa: E402
from iww_route_assignment.simulation import (  # noqa: E402
    Lock,
    Locks,
    LockSimulationSettings,
    LockSimulator,
    build_opentnsim_graph,
    inspect_trip,
)

from .conftest import make_network, make_trip, make_trips  # noqa: E402

TIME = AssignmentSettings(metric=RouteCostMetric.TRAVEL_TIME)
COST = AssignmentSettings(metric=RouteCostMetric.TRAVEL_COST_EUR)
START = datetime(2024, 1, 1, 8, 0)
HOUR = 3600.0


def _lock(chamber: ChamberSpec | None = None, at: tuple[float, float] = (5.05, 51.975)):
    chamber = chamber or ChamberSpec("c1", 40.0, 6.0, HOUR)
    return LockComplex("L1", "Test lock", (chamber,), *at)


def _network():
    return make_network(AC={"lock_count": 1, "lock_service_time_h": 0.25})


def _departing(minutes: list[float], **kwargs):
    trips = [
        dataclasses.replace(
            make_trip(f"t{i}", 0.0), departure_time=START + timedelta(minutes=m), **kwargs
        )
        for i, m in enumerate(minutes)
    ]
    return make_trips(trips)


def _run(
    trips,
    network,
    cost_inputs,
    catalogue,
    *,
    lock=None,
    settings=TIME,
    simulation: LockSimulationSettings | None = None,
    links=None,
):
    lock = lock or _lock()
    placements = place_locks(network, {"L1": lock}, links or {"NLAC": ("L1",)})
    simulator = LockSimulator({"L1": lock}, placements, simulation or LockSimulationSettings())
    return assign_traffic(
        trips, network, cost_inputs, catalogue, settings=settings, simulator=simulator
    )


def _route(result, trip_index: int) -> list[str]:
    edges = result.trip_edges
    return edges.loc[edges["voyage_index"] == trip_index, "arc_id"].tolist()


def test_without_a_simulator_the_result_is_the_plain_assignment(cost_inputs, catalogue) -> None:
    result = assign_traffic(_departing([0, 1]), _network(), cost_inputs, catalogue, settings=TIME)
    assert result.tables == {} and "simulation" not in result.summary
    assert "simulated" not in result.trips.columns
    assert "lock_waiting_h" not in result.trip_edges.columns
    assert result.trips["departure_time"].tolist() == [
        pd.Timestamp(START),
        pd.Timestamp(START + timedelta(minutes=1)),
    ]


def test_a_single_vessel_replaces_the_fixed_passage_time(cost_inputs, catalogue) -> None:
    trips = _departing([0])
    plain = assign_traffic(trips, _network(), cost_inputs, catalogue, settings=TIME)
    result = _run(trips, _network(), cost_inputs, catalogue)
    sailed, free_flow = result.trips.loc[0], plain.trips.loc[0]
    assert free_flow["reported_time_h"] == pytest.approx(1.75)
    assert sailed["lock_waiting_h"] == 0.0 and sailed["lock_service_h"] == pytest.approx(1.0)
    # 1.5 h sailing plus one hour in the chamber instead of the 0.25 h fixed time.
    assert sailed["reported_time_h"] == pytest.approx(2.5)
    assert _route(result, 0) == ["A|C", "C|D"]
    summary = result.summary["simulation"]
    assert summary["trips_simulated"] == 1 and summary["lock_passages"] == 1


def test_vessels_queue_for_the_chamber(cost_inputs, catalogue) -> None:
    simulation = LockSimulationSettings(reroute=False)
    result = _run(
        _departing([0, 1, 2, 3]), _network(), cost_inputs, catalogue, simulation=simulation
    )
    passages = result.tables["lock_passages"]
    # Vessel 0 crosses at once. The chamber then returns empty from side B (one hour), after
    # which each vessel needs a crossing of its own and the chamber a return trip (two hours).
    assert passages["wait_min"].tolist() == pytest.approx([0.0, 119.0, 238.0, 357.0])
    assert passages["service_min"].tolist() == pytest.approx([60.0] * 4)
    assert set(passages["side"]) == {"A"} and not passages["oversize"].any()
    trips = result.trips.set_index("trip_id")
    assert trips.loc["t3", "lock_waiting_h"] == pytest.approx(357 / 60)
    assert trips.loc["t3", "reported_time_h"] == pytest.approx(1.5 + 1.0 + 357 / 60)
    waits = result.trip_edges.groupby("arc_id")["lock_waiting_h"].sum()
    assert waits["A|C"] == pytest.approx(result.trips["lock_waiting_h"].sum())
    assert waits["C|D"] == 0.0
    assert result.intensity.set_index("arc_id").loc["A|C", "lock_waiting_h"] == pytest.approx(
        result.trips["lock_waiting_h"].sum()
    )
    lock_summary = result.tables["lock_summary"].iloc[0]
    assert lock_summary["passages"] == 4 and lock_summary["max_queue_a"] == 3
    assert lock_summary["max_wait_min"] == pytest.approx(357.0)


def test_waiting_is_priced_by_the_cost_function(cost_inputs, catalogue) -> None:
    # By cost the free-flow route is A-B-D, so the lock goes on A-B.
    network = make_network(AB={"lock_count": 1, "lock_service_time_h": 0.25})
    simulation = LockSimulationSettings(reroute=False)
    result = _run(
        _departing([0, 1]),
        network,
        cost_inputs,
        catalogue,
        lock=_lock(at=(5.05, 52.025)),
        settings=COST,
        simulation=simulation,
        links={"NLAB": ("L1",)},
    )
    first, second = result.trips.sort_values("trip_id").itertuples()
    assert first.lock_waiting_h == 0.0 and second.lock_waiting_h > 1.0
    assert second.reported_time_h == pytest.approx(first.reported_time_h + second.lock_waiting_h)
    assert second.reported_total_cost_eur > first.reported_total_cost_eur
    assert second.fuel_l > first.fuel_l


def test_vessels_from_both_sides_use_their_own_queue(cost_inputs, catalogue) -> None:
    forward = dataclasses.replace(make_trip("fwd", 0.0), departure_time=START)
    backward = dataclasses.replace(make_trip("back", 0.0), departure_time=START)
    trips = AssignmentTrips(
        (forward, backward),
        gpd.GeoSeries([Point(4.99, 52.0), Point(5.21, 52.0)], crs="EPSG:4326"),
        gpd.GeoSeries([Point(5.21, 52.0), Point(4.99, 52.0)], crs="EPSG:4326"),
    )
    simulation = LockSimulationSettings(reroute=False)
    result = _run(trips, _network(), cost_inputs, catalogue, simulation=simulation)
    passages = result.tables["lock_passages"].set_index("trip_id")
    assert passages.loc["fwd", "side"] == "A" and passages.loc["back", "side"] == "B"
    # fwd enters at 22.5 min and reaches side B at 82.5 min. back sails C-D first, reaches the
    # lock at 67.5 min and waits 15 min for the chamber to arrive.
    assert passages.loc["fwd", "wait_min"] == 0.0
    assert passages.loc["back", "wait_min"] == pytest.approx(15.0, abs=0.1)
    assert _route(result, 1) == ["C|D", "A|C"]


def test_a_long_queue_makes_later_vessels_take_the_other_route(cost_inputs, catalogue) -> None:
    result = _run(_departing([0, 60, 65, 70]), _network(), cost_inputs, catalogue)
    assert _route(result, 0) == ["A|C", "C|D"]
    for index in (1, 2, 3):
        assert _route(result, index) == ["A|B", "B|D"]
    reroutes = result.tables["reroutes"]
    assert reroutes["trip_id"].tolist() == ["t1", "t2", "t3"]
    assert set(reroutes["node_id"]) == {"A"} and (reroutes["saving_h"] >= 0.5).all()
    assert (reroutes["expected_wait_h"] >= 0.5).all()
    assert result.trips["n_reroutes"].tolist() == [0, 1, 1, 1]
    # The rerouted vessels no longer pass the lock, and are priced for the route they sailed.
    assert len(result.tables["lock_passages"]) == 1
    assert result.trips.loc[1, "reported_time_h"] == pytest.approx(2.0)
    assert result.summary["simulation"]["reroutes"] == 3


def test_a_small_gain_does_not_change_the_route(cost_inputs, catalogue) -> None:
    # The wait is 1.37 h and the saving 1.12 h. Neither passes a threshold of 1.5 h.
    for threshold in (1.2, 1.5):
        simulation = LockSimulationSettings(reroute_threshold_h=threshold)
        result = _run(
            _departing([0, 60]), _network(), cost_inputs, catalogue, simulation=simulation
        )
        assert result.tables["reroutes"].empty
        assert _route(result, 1) == ["A|C", "C|D"]
        assert len(result.tables["lock_passages"]) == 2


def test_trips_without_departure_time_or_outside_the_window_keep_their_route(
    cost_inputs, catalogue
) -> None:
    trips = _departing([0, 5, 600])
    undated = dataclasses.replace(make_trip("undated", 0.0))
    trips = AssignmentTrips(
        (*trips.trips, undated),
        gpd.GeoSeries([Point(4.99, 52.0)] * 4, crs="EPSG:4326"),
        gpd.GeoSeries([Point(5.21, 52.0)] * 4, crs="EPSG:4326"),
    )
    simulation = LockSimulationSettings(reroute=False, window_end=START + timedelta(hours=2))
    result = _run(trips, _network(), cost_inputs, catalogue, simulation=simulation)
    summary = result.summary["simulation"]
    assert summary["trips_simulated"] == 2
    assert summary["trips_outside_window"] == 1
    assert summary["trips_without_departure_time"] == 1
    by_id = result.trips.set_index("trip_id")
    assert by_id["simulated"].to_dict() == {"t0": True, "t1": True, "t2": False, "undated": False}
    assert by_id.loc["undated", "reported_time_h"] == pytest.approx(1.75)
    assert by_id.loc["undated", "lock_waiting_h"] == 0.0
    assert _route(result, 3) == ["A|C", "C|D"]


def test_trips_without_a_lock_are_not_simulated(cost_inputs, catalogue) -> None:
    trips = _departing([0, 1])
    network = make_network()  # no lock on any edge of the route A-B-D
    lock = _lock(at=(5.15, 51.99))
    result = _run(trips, network, cost_inputs, catalogue, lock=lock, links={"NLXX": ("L1",)})
    assert result.summary["simulation"]["trips_simulated"] == 0
    assert result.summary["simulation"]["trips_without_lock"] == 2
    assert result.tables["lock_passages"].empty and result.tables["lock_summary"].empty


def test_an_oversize_vessel_passes_alone_and_is_reported(cost_inputs, catalogue) -> None:
    tiny = _lock(ChamberSpec("c1", 30.0, 4.0, HOUR))
    result = _run(_departing([0, 1]), _network(), cost_inputs, catalogue, lock=tiny)
    passages = result.tables["lock_passages"]
    assert passages["oversize"].all() and len(passages) == 2
    assert passages["entry_time"].is_monotonic_increasing
    assert passages.loc[1, "entry_time"] >= passages.loc[0, "exit_time"]
    assert result.summary["simulation"]["oversize_passages"] == 2
    assert result.tables["lock_summary"].loc[0, "oversize_passages"] == 2


def test_penalised_lock_edges_are_avoided_in_the_simulation(cost_inputs, catalogue) -> None:
    restriction = ArcRestriction("A|C", "L1", StructureType.LOCK, "test", max_beam_m=4.0)
    trips = _departing([0])
    placements = place_locks(_network(), {"L1": _lock()}, {"NLAC": ("L1",)})
    simulator = LockSimulator({"L1": _lock()}, placements)
    result = assign_traffic(
        trips,
        _network(),
        cost_inputs,
        catalogue,
        [restriction],
        settings=TIME,
        simulator=simulator,
    )
    # The beam penalty already steers the free-flow route away from the lock.
    assert _route(result, 0) == ["A|B", "B|D"]
    assert result.summary["simulation"]["trips_without_lock"] == 1


def test_outputs_are_written(tmp_path, cost_inputs, catalogue) -> None:
    result = _run(_departing([0, 1]), _network(), cost_inputs, catalogue)
    paths = write_assignment_outputs(result, tmp_path)
    for name in ("lock_passages", "lock_summary", "reroutes"):
        assert paths[name].is_file()
    assert len(pd.read_parquet(paths["lock_passages"])) == 2
    assert '"lock_passages": 2' in paths["summary"].read_text()


def _custom_network(with_detour: bool) -> AssignmentNetwork:
    """O-S-A-D with a lock on A-D, a long direct S-D edge and optionally a detour A-X-D."""
    nodes = {"O": (5.0, 52.0), "S": (5.1, 52.0), "A": (5.2, 52.0), "D": (5.3, 52.0)}
    spec = {"O|S": (2.0, 10.0), "S|A": (5.0, 10.0), "A|D": (5.0, 10.0), "S|D": (40.0, 10.0)}
    if with_detour:
        nodes["X"] = (5.2, 52.1)
        spec |= {"A|X": (10.0, 10.0), "X|D": (10.0, 10.0)}
    edges = []
    for arc_id, (km, speed) in spec.items():
        u, v = arc_id.split("|")
        extra = {"lock_count": 1, "lock_service_time_h": 0.25} if arc_id == "A|D" else {}
        edges.append(
            NetworkEdge(arc_id, u, v, km, speed, section_code=arc_id.replace("|", ""), **extra)
        )
    return AssignmentNetwork(
        tuple(edges), tuple(nodes), np.array(list(nodes.values())), (None,) * len(edges)
    )


def _blockers_and_traveller(departure: datetime) -> tuple[AssignmentTrips, list[TripObservation]]:
    names = ["b0", "b1", "b2", "traveller"]
    starts = [
        START + timedelta(minutes=20),
        START + timedelta(minutes=21),
        START + timedelta(minutes=22),
        departure,
    ]
    trips = [
        dataclasses.replace(make_trip(name, 0.0), departure_time=start)
        for name, start in zip(names, starts, strict=True)
    ]
    origins = [(5.2, 52.0)] * 3 + [(5.0, 52.0)]
    return (
        AssignmentTrips(
            tuple(trips),
            gpd.GeoSeries([Point(p) for p in origins], crs="EPSG:4326"),
            gpd.GeoSeries([Point(5.3, 52.0)] * 4, crs="EPSG:4326"),
        ),
        trips,
    )


@pytest.mark.parametrize("with_detour", [True, False])
def test_a_vessel_reroutes_at_a_junction_ahead_but_never_turns_back(
    with_detour, cost_inputs, catalogue
) -> None:
    network = _custom_network(with_detour)
    lock = LockComplex("L1", "Lock", (ChamberSpec("c1", 40.0, 6.0, HOUR),), 5.25, 52.0)
    trips, _ = _blockers_and_traveller(START)
    placements = place_locks(network, {"L1": lock}, {"AD": ("L1",)})
    simulator = LockSimulator({"L1": lock}, placements)
    result = assign_traffic(
        trips, network, cost_inputs, catalogue, settings=TIME, simulator=simulator
    )
    traveller = result.trip_edges[result.trip_edges["trip_id"] == "traveller"]
    sailed = traveller["arc_id"].tolist()
    # The traveller passes S before the blockers reach the lock, so S sees no queue. At A the
    # queue is long. The direct S-D edge is behind the traveller, so it cannot be used.
    assert "S|D" not in sailed
    if with_detour:
        assert sailed == ["O|S", "S|A", "A|X", "X|D"]
        reroute = result.tables["reroutes"].iloc[0]
        assert reroute["trip_id"] == "traveller" and reroute["node_id"] == "A"
    else:
        assert sailed == ["O|S", "S|A", "A|D"]
        assert result.tables["reroutes"].empty
        assert result.trips.set_index("trip_id").loc["traveller", "lock_waiting_h"] > 2.0


def test_the_vessel_leaves_at_the_junction_when_the_queue_is_already_there(
    cost_inputs, catalogue
) -> None:
    # Leaving O at 08:40 the traveller meets S at 08:52, when the blockers are already queued.
    network = _custom_network(with_detour=True)
    lock = LockComplex("L1", "Lock", (ChamberSpec("c1", 40.0, 6.0, HOUR),), 5.25, 52.0)
    trips, _ = _blockers_and_traveller(START + timedelta(minutes=40))
    placements = place_locks(network, {"L1": lock}, {"AD": ("L1",)})
    result = assign_traffic(
        trips,
        network,
        cost_inputs,
        catalogue,
        settings=TIME,
        simulator=LockSimulator({"L1": lock}, placements),
    )
    traveller = result.trip_edges[result.trip_edges["trip_id"] == "traveller"]
    assert traveller["arc_id"].tolist() == ["O|S", "S|A", "A|X", "X|D"]
    reroute = result.tables["reroutes"].iloc[0]
    assert reroute["node_id"] == "S" and reroute["expected_wait_h"] == pytest.approx(5.72, abs=0.05)


def test_locks_work_as_an_opentnsim_edge_hook(cost_inputs, catalogue) -> None:
    """The lock hook also runs with OpenTNSim's own ``move`` on the generated graph."""
    network = _network()
    lock = _lock()
    placements = place_locks(network, {"L1": lock}, {"NLAC": ("L1",)})
    env = simpy.Environment()
    env.graph = env.FG = build_opentnsim_graph(network)
    locks = Locks(env, {"L1": lock}, placements, network)

    class Stock(core.Movable):
        L = 38.5
        B = 5.05
        name = "stock"

    vessel = Stock(
        env=env,
        v=20 / 3.6,
        geometry=env.graph.nodes["A"]["geometry"],
        route=["A", "C", "D"],
    )
    vessel.on_pass_edge_functions = [functools.partial(locks.pass_lock, vessel=vessel)]
    env.process(vessel.move())
    env.run()
    # 15 km + 15 km at 20 km/h, and one hour in the chamber.
    assert env.now == pytest.approx(2 * 2700 + HOUR)
    (passage,) = locks.passages
    assert passage.wait_s == 0 and passage.service_s == pytest.approx(HOUR)
    assert passage.arrival_s == pytest.approx(1350, abs=1)


def test_several_locks_on_one_edge_are_passed_in_order(cost_inputs, catalogue) -> None:
    network = make_network(AC={"lock_count": 2, "lock_service_time_h": 0.3})
    chamber = ChamberSpec("c1", 40.0, 6.0, HOUR)
    complexes = {
        "L1": LockComplex("L1", "First", (chamber,), 5.025, 51.9875),
        "L2": LockComplex("L2", "Second", (chamber,), 5.075, 51.9625),
    }
    placements = place_locks(network, complexes, {"NLAC": ("L1", "L2")})
    result = assign_traffic(
        _departing([0]),
        network,
        cost_inputs,
        catalogue,
        settings=TIME,
        simulator=LockSimulator(complexes, placements),
    )
    passages = result.tables["lock_passages"]
    assert passages["lock_id"].tolist() == ["L1", "L2"]
    # A quarter and then half of 15 km at 20 km/h (675 s and 1350 s), one hour in each chamber.
    assert (passages["arrival_time"] - pd.Timestamp(START)).dt.total_seconds().tolist() == (
        pytest.approx([675.0, 675.0 + HOUR + 1350.0], abs=2)
    )
    assert result.trips.loc[0, "reported_time_h"] == pytest.approx(1.5 + 2.0, abs=0.001)
    assert placements.report["edges_with_several_locks"] == 1


def test_simulator_is_built_from_the_euris_folder(tmp_path, cost_inputs, catalogue) -> None:
    chambers = gpd.GeoDataFrame(
        {
            "id": ["c1"],
            "lock_id": ["L1"],
            "dim_usable_length": [40.0],
            "dim_gate_width": [6.0],
            "otimemin": [60.0],
            "mdraughtcm": [300],
        },
        geometry=[Point(5.05, 51.975).buffer(0.0001)],
        crs="EPSG:4326",
    )
    chambers.to_parquet(tmp_path / "chambers.geoparquet")
    locks = gpd.GeoDataFrame(
        {"id": ["L1"], "name": ["Test lock"]}, geometry=[Point(5.05, 51.975)], crs="EPSG:4326"
    )
    locks.to_parquet(tmp_path / "locks.geoparquet")
    pd.DataFrame({"section_id": ["NLAC"], "lock_id": ["L1"], "bridge_id": [""]}).to_parquet(
        tmp_path / "edges.geoparquet"
    )
    pd.DataFrame(
        {
            "bridge_id": ["b"],
            "dim_gate_width": [5.0],
            "dim_height": [4.0],
            "cl_height": [None],
            "mheightcmc": [None],
            "operable": [0],
        }
    ).to_parquet(tmp_path / "openings.geoparquet")

    network = _network()
    simulator = LockSimulator.from_directory(tmp_path, network)
    result = assign_traffic(
        _departing([0]), network, cost_inputs, catalogue, settings=TIME, simulator=simulator
    )
    passage = result.tables["lock_passages"].iloc[0]
    assert passage["lock_name"] == "Test lock" and passage["service_min"] == pytest.approx(60.0)
    summary = result.summary["simulation"]
    assert summary["lock_data"]["chambers"] == 1 and summary["placement"]["placed"] == 1


@pytest.mark.parametrize(
    "options",
    [
        {"reroute_threshold_h": -1.0},
        {"reroute_threshold_h": float("nan")},
        {"start_side": "C"},
        {"lookahead_h": 0.0},
        {"lookahead_h": -1.0},
        {"lookahead_h": float("nan")},
        {"window_start": START, "window_end": START},
    ],
)
def test_invalid_simulation_settings_are_rejected(options) -> None:
    with pytest.raises(ValidationError):
        LockSimulationSettings(**options)


def test_inspect_trip_shows_planned_and_sailed_route(cost_inputs, catalogue) -> None:
    network = _network()
    result = _run(_departing([0, 60, 65, 70]), network, cost_inputs, catalogue)
    planned = result.tables["planned_edges"]
    assert planned.groupby("trip_id")["arc_id"].apply(list).to_dict() == {
        f"t{i}": ["A|C", "C|D"] for i in range(4)
    }
    rerouted = inspect_trip(result, network, "t1")
    by_route = rerouted.groupby("route")["arc_id"].apply(list).to_dict()
    assert by_route == {"planned": ["A|C", "C|D"], "sailed": ["A|B", "B|D"]}
    assert not rerouted["shared"].any() and rerouted.geometry.notna().all()
    totals = rerouted.groupby("route")["free_flow_time_h"].sum()
    assert totals["planned"] == pytest.approx(1.75) and totals["sailed"] == pytest.approx(2.0)
    assert rerouted["penalty_h"].eq(0.0).all()
    assert rerouted.groupby("route")["lock_count"].sum().to_dict() == {"planned": 1, "sailed": 0}
    straight = inspect_trip(result, network, "t0")
    assert straight["shared"].all()
    with pytest.raises(ValidationError):
        inspect_trip(result, network, "missing")


def test_lookahead_defaults_to_eight_hours_and_can_be_switched_off() -> None:
    assert LockSimulationSettings().lookahead_h == 8.0
    assert LockSimulationSettings(lookahead_h=None).lookahead_h is None


def test_a_distant_queue_is_not_seen_within_a_short_lookahead(cost_inputs, catalogue) -> None:
    """The lock is 0.5 h of sailing beyond S. With 0.3 h of lookahead the vessel only sees the
    queue at the next junction A. Without a limit it already reacts at S."""
    network = _custom_network(with_detour=True)
    lock = LockComplex("L1", "Lock", (ChamberSpec("c1", 40.0, 6.0, HOUR),), 5.25, 52.0)
    placements = place_locks(network, {"L1": lock}, {"AD": ("L1",)})
    trips, _ = _blockers_and_traveller(START + timedelta(minutes=40))

    def node_of_reroute(lookahead_h: float | None) -> str:
        simulation = LockSimulationSettings(lookahead_h=lookahead_h)
        result = assign_traffic(
            trips,
            network,
            cost_inputs,
            catalogue,
            settings=TIME,
            simulator=LockSimulator({"L1": lock}, placements, simulation),
        )
        rerouted = result.tables["reroutes"].query("trip_id == 'traveller'")
        assert result.trip_edges.query("trip_id == 'traveller'")["arc_id"].tolist() == [
            "O|S",
            "S|A",
            "A|X",
            "X|D",
        ]
        return str(rerouted.iloc[0]["node_id"])

    assert node_of_reroute(None) == "S"
    assert node_of_reroute(8.0) == "S"
    assert node_of_reroute(0.3) == "A"


def _grid_case(size: int = 6, lock_every: int = 3):
    """A grid of 3 km edges at 10 km/h with a one-vessel-per-crossing lock on every few edges."""
    nodes = {
        f"n{i}_{j}": (5.0 + 0.04 * i, 52.0 + 0.03 * j) for i in range(size) for j in range(size)
    }
    pairs = [
        (f"n{i}_{j}", f"n{i + di}_{j + dj}")
        for i in range(size)
        for j in range(size)
        for di, dj in ((1, 0), (0, 1))
        if i + di < size and j + dj < size
    ]
    edges, complexes, links = [], {}, {}
    chamber = ChamberSpec("c1", 40.0, 6.0, HOUR)
    for k, (u, v) in enumerate(pairs):
        has_lock = k % lock_every == 0
        extra = {"lock_count": 1, "lock_service_time_h": 0.25} if has_lock else {}
        edges.append(NetworkEdge(f"{u}|{v}", u, v, 3.0, 10.0, section_code=f"S{k}", **extra))
        if has_lock:
            (x1, y1), (x2, y2) = nodes[u], nodes[v]
            complexes[f"L{k}"] = LockComplex(
                f"L{k}", f"Lock {k}", (chamber,), (x1 + x2) / 2, (y1 + y2) / 2
            )
            links[f"S{k}"] = (f"L{k}",)
    network = AssignmentNetwork(
        tuple(edges), tuple(nodes), np.array(list(nodes.values())), (None,) * len(edges)
    )
    return network, complexes, links, nodes


@pytest.mark.parametrize("lookahead_h", [None, 4.0])
@pytest.mark.parametrize("metric", [TIME, COST])
def test_corridor_search_makes_the_same_decisions_as_a_search_of_the_whole_network(
    metric, lookahead_h, cost_inputs, catalogue
) -> None:
    network, complexes, links, nodes = _grid_case()
    placements = place_locks(network, complexes, links)
    rng = np.random.default_rng(3)
    names = list(nodes)
    trips, origins, destinations = [], [], []
    for i in range(70):
        origin, destination = rng.choice(len(names), 2, replace=False)
        trips.append(
            dataclasses.replace(
                make_trip(f"g{i}", float(rng.uniform(0, 300))),
                departure_time=START + timedelta(minutes=float(rng.uniform(0, 240))),
            )
        )
        origins.append(Point(nodes[names[origin]]))
        destinations.append(Point(nodes[names[destination]]))
    grid_trips = AssignmentTrips(
        tuple(trips),
        gpd.GeoSeries(origins, crs="EPSG:4326"),
        gpd.GeoSeries(destinations, crs="EPSG:4326"),
    )
    settings = dataclasses.replace(metric, points_crs="EPSG:4326")

    def run(exhaustive: bool):
        simulation = LockSimulationSettings(lookahead_h=lookahead_h, exhaustive_search=exhaustive)
        return assign_traffic(
            grid_trips,
            network,
            cost_inputs,
            catalogue,
            settings=settings,
            simulator=LockSimulator(complexes, placements, simulation),
        )

    corridor, exhaustive = run(False), run(True)
    assert len(exhaustive.tables["reroutes"]) > 0  # the comparison needs real decisions
    columns = ["trip_id", "seq", "arc_id"]
    pd.testing.assert_frame_equal(
        corridor.trip_edges[columns], exhaustive.trip_edges[columns], check_dtype=False
    )
    pd.testing.assert_frame_equal(corridor.tables["reroutes"], exhaustive.tables["reroutes"])
    pd.testing.assert_frame_equal(
        corridor.tables["lock_passages"], exhaustive.tables["lock_passages"]
    )


def test_a_cached_wait_estimate_follows_the_queue(cost_inputs, catalogue) -> None:
    env = simpy.Environment()
    complex_ = LockComplex("1", "Lock", (ChamberSpec("c1", 40, 6, 1800),))
    lock = Lock(env, complex_)
    chamber = lock.chambers[0]

    class Boat:
        def __init__(self, name: str) -> None:
            self.name, self.L, self.B = name, 38.0, 5.0

    # Idle on side A: no wait on A, and one crossing for a vessel that joins B. These are cached.
    assert chamber.expected_wait_s("A", 38.0, 5.0) == 0.0
    assert chamber.expected_wait_s("B", 38.0, 5.0) == 1800.0
    chamber.submit(Boat("a"), "B")
    chamber.submit(Boat("b"), "B")
    env.run(until=1)
    # The chamber is crossing to B and arrives in 1799 s. Only one 38 m vessel fits per load.
    # A: wait for the arrival, then a crossing back = 3599 s.
    assert chamber.expected_wait_s("A", 38.0, 5.0) == pytest.approx(3599.0)
    # B: the arrival (1799 s) and then three loads (a, b, and the new vessel) with a full
    # round trip of 3600 s for each of the two loads ahead = 1799 + 7200 s. A stale cache would
    # still say 1800 s.
    assert chamber.expected_wait_s("B", 38.0, 5.0) == pytest.approx(1799.0 + 7200.0)
    env.run()
    # Both vessels are through and the chamber rests on side A.
    assert chamber.expected_wait_s("A", 38.0, 5.0) == 0.0
    assert chamber.expected_wait_s("B", 38.0, 5.0) == 1800.0
