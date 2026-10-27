"""Synthetic four-node network with two routes from A to D.

Route 1, A-B-D: 20 km at 10 km/h, 2 h. Route 2, A-C-D: 30 km at 20 km/h, 1.5 h.
Time picks route 2. With 10 EUR/km distance cost, cost picks route 1 for M1.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely import Point

from iww_route_assignment import (
    AssignmentNetwork,
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
