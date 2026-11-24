"""Synthetic four-node network with two routes from A to D.

Route 1, A-B-D: 20 km at 10 km/h, 2 h. Route 2, A-C-D: 30 km at 20 km/h, 1.5 h.
Time picks route 2. With 10 EUR/km distance cost, cost picks route 1 for M1.
"""

from __future__ import annotations

import pickle
import shutil
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pytest
from shapely import Point

from iww_route_assignment import (
    AssignmentConfig,
    AssignmentNetwork,
    AssignmentPaths,
    AssignmentSettings,
    AssignmentTrips,
    CostInputs,
    CostRates,
    NetworkEdge,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    TripObservation,
    VesselCatalogue,
)
from iww_route_assignment.io import load_fuel_observations_csv, load_vessel_references_csv

FIXTURES = Path(__file__).parents[1] / "fixtures"
NODES = {"A": (5.0, 52.0), "B": (5.1, 52.05), "C": (5.1, 51.95), "D": (5.2, 52.0)}


def make_network(**edge_overrides: dict[str, object]) -> AssignmentNetwork:
    spec = {
        "A|B": ("A", "B", 10.0, 10.0),
        "B|D": ("B", "D", 10.0, 10.0),
        "A|C": ("A", "C", 15.0, 20.0),
        "C|D": ("C", "D", 15.0, 20.0),
    }
    edges = []
    for arc_id, (u, v, km, speed) in spec.items():
        options = {"section_code": f"NL{arc_id.replace('|', '')}", "waterway_cemt_class": "I"}
        options |= edge_overrides.get(arc_id.replace("|", ""), {})
        edges.append(NetworkEdge(arc_id, u, v, km, speed, **options))  # type: ignore[arg-type]
    return AssignmentNetwork(
        tuple(edges),
        tuple(NODES),
        np.array(list(NODES.values())),
        (None,) * len(edges),
        "synthetic-test",
    )


def make_rates(appearance: str = "droge_bulk", vessel: str = "M1") -> CostRates:
    return CostRates(
        appearance=appearance,
        vessel_rws_class=vessel,
        labour_eur_per_h=10.0,
        labour_waiting_eur_per_h=7.0,
        maintenance_eur_per_h=2.0,
        maintenance_eur_per_km=10.0,
        insurance_eur_per_h=1.0,
        interest_eur_per_percent_h=0.5,
        depreciation_eur_per_h=4.0,
        port_dues_eur_per_h=5.0,
        other_eur_per_h=6.0,
        loading_eur_per_t=2.0,
        unloading_eur_per_t=3.0,
        source="synthetic-test",
    )


def make_trip(
    trip_id: str, cargo_t: float, *, vessel: str = "M1", appearance: str = "droge_bulk"
) -> TripObservation:
    return TripObservation(
        trip_id,
        "NLAAA",
        "NLDDD",
        None,
        350.0,
        cargo_t,
        None,
        "",
        "synthetic-test",
        vessel_rws_class=vessel,
        appearance=appearance,
        appearance_source="synthetic-test",
    )


def make_trips(
    trips: list[TripObservation],
    origin: tuple[float, float] = (4.99, 52.0),
    destination: tuple[float, float] = (5.21, 52.0),
) -> AssignmentTrips:
    count = len(trips)
    return AssignmentTrips(
        tuple(trips),
        gpd.GeoSeries([Point(origin)] * count, crs="EPSG:4326"),
        gpd.GeoSeries([Point(destination)] * count, crs="EPSG:4326"),
    )


@pytest.fixture
def network() -> AssignmentNetwork:
    return make_network()


@pytest.fixture
def scenario() -> ScenarioCostParameters:
    return ScenarioCostParameters(
        fuel_price_eur_per_l=2.0,
        interest_rate_percent=4.0,
        cargo_waiting_time_h=2.0,
        idle_fuel_factor=0.1,
    )


@pytest.fixture
def catalogue() -> VesselCatalogue:
    return VesselCatalogue(load_vessel_references_csv(FIXTURES / "vessel_references.csv"))


@pytest.fixture
def cost_inputs(scenario: ScenarioCostParameters, catalogue: VesselCatalogue) -> CostInputs:
    provider = TableFuelConsumptionProvider(
        load_fuel_observations_csv(FIXTURES / "fuel_observations.csv"), catalogue.references
    )
    return CostInputs((make_rates(), make_rates("overig")), provider, scenario)


# A whole run on files -----------------------------------------------------------------------
# Four EuRIS-style sections. A-B-D: 20 km at 10 km/h plus one lock of 0.5 h, so 2.5 h.
# A-C-D: 30 km at 20 km/h, so 1.5 h. Time picks C, cost picks B (10 EUR/km distance cost).
WORKFLOW_SCENARIO = ScenarioCostParameters(
    fuel_price_eur_per_l=2.0,
    interest_rate_percent=4.0,
    cargo_waiting_time_h=2.0,
    idle_fuel_factor=0.1,
)
WORKFLOW_COST_ROWS = (
    "appearance,rws_class,labor_h,labor_wait_h,maint_h,maint_km,insurance_h,interest_pct_h,"
    "depreciation_h,harbor_h,other_h,load_t,unload_t,source\n"
    "droge_bulk,M1,10,7,2,10,1,0.5,4,5,6,2,3,synthetic-test\n"
    "overig,M1,10,7,2,10,1,0.5,4,5,6,2,3,synthetic-test\n"
)
WORKFLOW_STATES = (
    "vessel_id,length_m,beam_m,draught_m,air_draught_m,carries_dangerous_goods,source\n"
    "M1-loaded,38.5,5.05,2.2,4.0,false,synthetic-test\n"
    "M1-empty,38.5,5.05,1.0,5.0,false,synthetic-test\n"
)


def workflow_trip_rows() -> pd.DataFrame:
    """Nine source rows: four routed, one unknown class, one without rates, three dropped."""
    rows = [
        # id, class, loading code, segment, cargo, departure
        ("a", "M1", "7", "Droge bulkvaart", "175", "2026-03-01 08:00:00"),
        ("b", "M1", "1", "Overig", None, "2026-03-10 09:30:00"),
        ("c", "M1", "7", "Droge bulkvaart", "100", "2026-04-01 00:00:00"),
        ("d", "B04", "7", "Droge bulkvaart", "100", "2026-03-20 12:00:00"),
        ("e", "M1", "7", "Droge bulkvaart", "50", "2026-03-21 12:00:00"),
        ("e", "M1", "7", "Droge bulkvaart", "60", "2026-03-22 12:00:00"),
        ("f", "M1", "7", "Droge bulkvaart", "x", "2026-03-23 12:00:00"),
        ("g", "M1", "7", "Containervaart", "100", "2026-03-24 12:00:00"),
        ("h", "M1", "7", "Droge bulkvaart", "350", "2026-02-27 23:59:00"),
    ]
    count = len(rows)
    return pd.DataFrame(
        {
            "Unnamed: 0": [str(i) for i in range(count)],
            "Jaar": ["2026"] * count,
            "Pseudo_ReisID": [r[0] for r in rows],
            "SK_CODE": [r[1] for r in rows],
            "v28_Beladingscode": [r[2] for r in rows],
            "Segment": [r[3] for r in rows],
            "Lading": [r[4] for r in rows],
            "Aantal_Schepen": ["1"] * count,
            "Laadvermogen": ["350"] * count,
            "UNLO_Herkomst": ["NLAAA"] * count,
            "UNLO_Bestemming": ["NLDDD"] * count,
            "Eerste_Geom": ["POINT (4.99 52)"] * count,
            "Laatste_Geom": ["POINT (5.21 52)"] * count,
            "Eerste_Tijdstip": [r[5] for r in rows],
        },
        dtype="string",
    )


def workflow_graph() -> nx.Graph:
    graph = nx.Graph()
    for node, (lon, lat) in NODES.items():
        graph.add_node(node, geometry=Point(lon, lat))
    for u, v, km, speed, code in (
        ("A", "B", 10, 10.0, "NL01"),
        ("B", "D", 10, 10.0, "NL02"),
        ("A", "C", 15, 20.0, "NL03"),
        ("C", "D", 15, 20.0, "NL04"),
    ):
        graph.add_edge(
            u,
            v,
            length_m=km * 1000,
            maxspeed_up=speed,
            code=code,
            cemt="I",
            mwidthcm=1000,
            mdraughtcm=300,
            ww_name=f"Kanaal {code}",
        )
    return graph


@dataclass(frozen=True)
class WorkflowFiles:
    """Input files of one synthetic run, written under a temporary folder."""

    trips: Path
    graph: Path
    structures: Path
    reference: Path


@pytest.fixture
def workflow_files(tmp_path: Path) -> WorkflowFiles:
    """Trips, EuRIS-style network with one shallow lock on B-D, and a reference folder."""
    trips = tmp_path / "ivs_trips_test.csv"
    workflow_trip_rows().to_csv(trips, index=False)
    graph = tmp_path / "graph.pickle"
    graph.write_bytes(pickle.dumps(workflow_graph()))
    structures = tmp_path / "dropins"
    structures.mkdir()
    tables = {
        "edges": pd.DataFrame(
            {"section_id": ["NL02"], "lock_id": ["NLLOCK1"], "bridge_id": [None]}
        ),
        # The lock sill is 2.0 m deep: a loaded M1 (2.2 m) does not fit, an empty one does.
        "chambers": pd.DataFrame(
            {
                "lock_id": ["NLLOCK1"],
                "dim_gate_width": [12.0],
                "dim_usable_length": [100.0],
                "mdraughtcm": [200.0],
            }
        ),
        "openings": pd.DataFrame(
            {
                "bridge_id": pd.Series([], dtype="string"),
                "dim_gate_width": pd.Series([], dtype="float64"),
                "dim_height": pd.Series([], dtype="float64"),
                "cl_height": pd.Series([], dtype="float64"),
                "mheightcmc": pd.Series([], dtype="float64"),
                "operable": pd.Series([], dtype="int64"),
            }
        ),
    }
    for name, table in tables.items():
        table.to_parquet(structures / f"{name}.geoparquet")
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "cost_rates.csv").write_text(WORKFLOW_COST_ROWS, encoding="utf-8")
    (reference / "navigability_vessels.csv").write_text(WORKFLOW_STATES, encoding="utf-8")
    for name in ("fuel_observations.csv", "vessel_references.csv"):
        shutil.copyfile(FIXTURES / name, reference / name)
    return WorkflowFiles(trips, graph, structures, reference)


def workflow_paths(files: WorkflowFiles) -> AssignmentPaths:
    return AssignmentPaths(
        trips=files.trips,
        graph=files.graph,
        reference=files.reference,
        structures=files.structures,
    )


def workflow_config(metric: str = "travel_cost_eur", **options: object) -> AssignmentConfig:
    return AssignmentConfig(
        scenario=WORKFLOW_SCENARIO,
        cargo_weight_unit="t",
        settings=AssignmentSettings(metric=metric, points_crs="EPSG:4326"),  # type: ignore[arg-type]
        **options,  # type: ignore[arg-type]
    )
