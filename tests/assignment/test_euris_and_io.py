"""EuRIS adapter, trip reading and output writing on small synthetic tables."""

from __future__ import annotations

import json
import pickle

import networkx as nx
import numpy as np
import pandas as pd
import pytest
from shapely import LineString, Point

from iww_route_assignment import (
    ArcRestriction,
    ArcTravelInput,
    AssignmentSettings,
    DuplicateTripRule,
    NavigabilityPenalties,
    NavigabilityVessel,
    RoutePreferences,
    StructureType,
    ValidationError,
    assign_traffic,
    calculate_arc_costs,
)
from iww_route_assignment.assignment import penalty_profile
from iww_route_assignment.costs import calculate_arc_cost_arrays
from iww_route_assignment.domain.enums import PenaltyType
from iww_route_assignment.io import (
    EurisLimitRules,
    build_euris_inputs,
    build_euris_structures,
    load_euris_graph,
    load_euris_inputs,
    read_assignment_trips,
    write_assignment_outputs,
)
from iww_route_assignment.navigability import build_restriction_index

from .conftest import make_network, make_trip, make_trips

LINKS = pd.DataFrame(
    {
        "section_id": ["NL01", "NL01", "NL02", None],
        "lock_id": ["NLLOCK", None, "NLEMPTY", "XX"],
        "bridge_id": [None, "NLBRIDGE", None, None],
    }
)
CHAMBERS = pd.DataFrame(
    {
        "lock_id": ["NLLOCK", "NLLOCK"],
        "dim_gate_width": [9.0, 12.0],
        "dim_usable_length": [100.0, 0.0],
        "mdraughtcm": [300.0, np.nan],
    }
)
OPENINGS = pd.DataFrame(
    {
        "bridge_id": ["NLBRIDGE", "NLBRIDGE"],
        "dim_gate_width": [10.0, 14.0],
        "dim_height": [np.nan, 6.5],
        "cl_height": [700.0, np.nan],
        "mheightcmc": [np.nan, np.nan],
        "operable": [0, 0],
    }
)


def _graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("A", geometry=Point(5.0, 52.0))
    graph.add_node("B", geometry=Point(5.1, 52.0))
    graph.add_node("C", geometry=Point(5.2, 52.0))
    graph.add_edge(
        "A",
        "B",
        length_m=10000.0,
        code="NL01",
        cemt="Va",
        mwidthcm="1140",
        mdraughtcm=350.0,
        mlengthcm=11000.0,
        maxspeed_up="12 km/h",
        ww_name="Maas",
        geometry=LineString([(5.0, 52.0), (5.1, 52.0)]),
    )
    graph.add_edge("B", "C", length_m=5000.0, code="NL02", cemt="0", ww_name="Noordzee")
    return graph


def test_structures_take_widest_longest_and_highest() -> None:
    structures = build_euris_structures(LINKS, CHAMBERS, OPENINGS)
    lock = structures.locks["NLLOCK"]
    assert (lock.max_beam_m, lock.max_length_m, lock.max_draught_m) == (12.0, 100.0, 3.0)
    bridge = structures.bridges["NLBRIDGE"]
    # Heights: 700 cm and 6.5 m; the higher opening sets the limit.
    assert (bridge.max_beam_m, bridge.max_air_draught_m, bridge.movable) == (14.0, 7.0, False)
    assert structures.locks["NLEMPTY"].chamber_count == 0
    assert structures.section_locks["NL01"] == ("NLLOCK",)


def test_movable_bridge_has_no_height_limit() -> None:
    openings = OPENINGS.assign(operable=[0, 1])
    bridge = build_euris_structures(LINKS, CHAMBERS, openings).bridges["NLBRIDGE"]
    assert bridge.max_air_draught_m is None


def test_build_inputs_reads_units_and_links_structures() -> None:
    structures = build_euris_structures(LINKS, CHAMBERS, OPENINGS)
    inputs = build_euris_inputs(_graph(), structures)
    first, second = inputs.network.edges
    assert first.arc_id == "A|B" and first.speed_km_per_h == 12.0
    assert first.lock_count == 1 and first.lock_service_time_h == 0.5
    assert second.waterway_cemt_class is None and second.lock_count == 1
    by_structure = {r.structure_id: r for r in inputs.restrictions}
    fairway = by_structure["NL01"]
    assert (fairway.max_beam_m, fairway.max_draught_m, fairway.max_length_m) == (11.4, 3.5, None)
    assert by_structure["NLBRIDGE"].max_air_draught_m == 7.0
    assert inputs.report["restrictions_by_type"] == {"bridge": 1, "lock": 1, "waterway": 1}
    with_length = build_euris_inputs(_graph(), structures, EurisLimitRules(fairway_length=True))
    assert {r.structure_id: r for r in with_length.restrictions}["NL01"].max_length_m == 110.0


def test_graph_loader_rejects_multigraph_and_missing_length(tmp_path) -> None:
    path = tmp_path / "graph.pickle"
    path.write_bytes(pickle.dumps(nx.MultiGraph()))
    with pytest.raises(ValidationError, match="MultiGraph"):
        load_euris_graph(path)
    graph = _graph()
    graph.add_edge("A", "C")
    with pytest.raises(ValidationError, match="length_m"):
        build_euris_inputs(graph)


def test_load_inputs_from_files(tmp_path) -> None:
    (tmp_path / "graph.pickle").write_bytes(pickle.dumps(_graph()))
    folder = tmp_path / "dropins"
    folder.mkdir()
    for name, table in (("edges", LINKS), ("chambers", CHAMBERS), ("openings", OPENINGS)):
        table.to_parquet(folder / f"{name}.geoparquet")
    inputs = load_euris_inputs(tmp_path / "graph.pickle", folder)
    assert len(inputs.restrictions) == 3
    (folder / "openings.geoparquet").unlink()
    with pytest.raises(ValidationError, match="openings"):
        load_euris_inputs(tmp_path / "graph.pickle", folder)


def test_penalty_rules_match_previous_assignment(catalogue) -> None:
    network = make_network(BD={"water_name": "North Sea"}, CD={"waterway_cemt_class": None})
    restrictions = build_restriction_index(
        [
            ArcRestriction("A|B", "NLAB", StructureType.WATERWAY, "t", max_beam_m=5.0),
            ArcRestriction("A|B", "NLLOCK", StructureType.LOCK, "t", max_beam_m=4.0),
            ArcRestriction(
                "A|C", "BELOCK", StructureType.LOCK, "t", max_beam_m=4.05, max_length_m=30.0
            ),
        ]
    )
    vessel = NavigabilityVessel("m1", length_m=38.5, beam_m=5.05, draught_m=1.0)
    profile = penalty_profile(
        network, restrictions, vessel, "Va", NavigabilityPenalties(), RoutePreferences()
    )
    hours = {kind: values.tolist() for kind, values in profile.hours.items()}
    # A-B: tightest beam is the Dutch lock, one flat 1000 h, not one per structure.
    # A-C: Belgian lock, 5 h x 1.0 m, and 1000 h for length.
    assert hours[PenaltyType.BEAM] == [1000.0, 0.0, pytest.approx(5.0), 0.0]
    assert hours[PenaltyType.LENGTH] == [0.0, 0.0, 1000.0, 0.0]
    # Vessel class Va is above waterway class I on every edge that has a class.
    assert hours[PenaltyType.CEMT] == [1000.0, 1000.0, 1000.0, 0.0]
    assert hours[PenaltyType.SEA] == [0.0, 100000.0, 0.0, 0.0]
    assert profile.details[0][0].structure_id == "NLLOCK"


def test_undetermined_air_draught_has_its_own_penalty(catalogue) -> None:
    network = make_network()
    restrictions = build_restriction_index(
        [ArcRestriction("A|B", "NLBR", StructureType.BRIDGE, "t", max_air_draught_m=5.0)]
    )
    vessel = NavigabilityVessel("m1", length_m=38.5, beam_m=5.05, draught_m=1.0)
    penalties = NavigabilityPenalties(undetermined_h=7.0)
    profile = penalty_profile(network, restrictions, vessel, "I", penalties, RoutePreferences())
    assert profile.hours[PenaltyType.UNDETERMINED].tolist() == [7.0, 0.0, 0.0, 0.0]


def test_array_core_matches_batch_calculator(cost_inputs) -> None:
    rates = cost_inputs.rates_for("M1", "droge_bulk")
    scenario = cost_inputs.scenario
    fuel = cost_inputs.fuel_provider.resolve("M1", "I", loading_ratio=0.3)
    arcs = [
        ArcTravelInput(12.0, 9.0, lock_service_time_h=0.5, relaxation_penalty_time_h=3.0),
        ArcTravelInput(0.0, 1.0, bridge_service_time_h=0.25, calibration_delay_time_h=1.0),
    ]
    batch = calculate_arc_costs(arcs, rates, scenario, [fuel, fuel])
    arrays = calculate_arc_cost_arrays(
        [12.0, 0.0],
        [9.0, 1.0],
        [0.5, 0.25],
        [3.0, 1.0],
        [fuel.fuel_rate_l_per_h] * 2,
        rates,
        scenario,
    )
    for name in ("reported_variable_cost_eur", "choice_cost_eur", "idle_fuel_l"):
        assert getattr(arrays, name).tolist() == [getattr(b, name) for b in batch]
    with pytest.raises(ValidationError):
        calculate_arc_cost_arrays([1.0], [np.nan], [0.0], [0.0], [1.0], rates, scenario)


def _extended_rows() -> pd.DataFrame:
    rows = {
        "Unnamed: 0": ["0", "1", "2", "3", "4"],
        "Jaar": ["2026"] * 5,
        "Pseudo_ReisID": ["a", "b", "b", "c", "d"],
        "SK_CODE": ["M1", "M1", "M1", "M1", "M1"],
        "v28_Beladingscode": ["7", "7", "1", "1", "7"],
        "Segment": ["Droge bulkvaart", "Tankvaart", "Tankvaart", "Overig", "Containervaart"],
        "Lading": ["100", "50", None, None, "x"],
        "Aantal_Schepen": ["1"] * 5,
        "Laadvermogen": ["350"] * 5,
        "UNLO_Herkomst": ["NLAAA"] * 5,
        "UNLO_Bestemming": ["NLDDD"] * 5,
        "Eerste_Geom": ["POINT (4.99 52)", "POINT (4.99 52)", "", "not wkt", "POINT (4.99 52)"],
        "Laatste_Geom": ["POINT (5.21 52)"] * 5,
    }
    return pd.DataFrame(rows, dtype="string")


def test_read_trips_excludes_duplicates_and_maps_overig() -> None:
    settings = AssignmentSettings(points_crs="EPSG:4326")
    trips = read_assignment_trips(
        _extended_rows(),
        source_dataset="test",
        format="extended_2026",
        cargo_weight_unit="t",
        drop_export_index=True,
        duplicate_trip_ids=DuplicateTripRule.EXCLUDE,
        settings=settings,
    )
    assert [t.source_row_id for t in trips.trips] == ["test:2026:a", "test:2026:c"]
    assert trips.trips[1].appearance == "overig"
    assert trips.excluded_duplicate_rows == 2
    assert len(trips.rejections) == 1
    assert trips.origins.iloc[0] == Point(4.99, 52.0)
    assert trips.origins.iloc[1] is None
    with pytest.raises(ValidationError, match="Duplicate"):
        read_assignment_trips(
            _extended_rows(),
            source_dataset="test",
            cargo_weight_unit="t",
            drop_export_index=True,
            settings=settings,
        )


def test_write_outputs(tmp_path, network, cost_inputs, catalogue) -> None:
    restriction = ArcRestriction("A|C", "DELOCK", StructureType.LOCK, "t", max_beam_m=4.55)
    result = assign_traffic(
        make_trips([make_trip("a", 0.0), make_trip("b", 175.0, vessel="B04")]),
        network,
        cost_inputs,
        catalogue,
        [restriction],
        settings=AssignmentSettings(metric="travel_time"),
    )
    paths = write_assignment_outputs(result, tmp_path, metadata={"settings": AssignmentSettings()})
    assert pd.read_parquet(paths["trips"])["status"].tolist() == ["success", "vessel_fail"]
    assert len(pd.read_parquet(paths["trip_edges"])) == 2
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["missing_vessel_classes"] == ["B04"]
    assert summary["metadata"]["settings"]["metric"] == "travel_cost_eur"
    assert paths["gpkg"].is_file()
    assert pd.read_csv(paths["rejections"]).empty
