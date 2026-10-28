"""Hand-calculated verification cases for traffic assignment.

Network: route 1 A-B-D is 20 km at 10 km/h (2 h), route 2 A-C-D is 30 km at 20 km/h
(1.5 h), all on CEMT class I. Vessel M1, capacity 350 t, beam 5.05 m. Rates: hourly
30 EUR/h (labour 10, maintenance 2, insurance 1, interest 4 % x 0.5, depreciation 4,
port 5, other 6), 10 EUR/km, loading 2 EUR/t, unloading 3 EUR/t, waiting labour 7 EUR/h.
Scenario: fuel 2 EUR/l, waiting 2 h. Fuel M1 on class I: 40, 60 and 80 l/h at loading
ratio 0, 0.5 and 1.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
from shapely import Point

from iww_route_assignment import (
    ArcRestriction,
    AssignmentNetwork,
    AssignmentSettings,
    AssignmentTrips,
    CostInputs,
    CostRates,
    FuelConsumptionObservation,
    NetworkEdge,
    RouteCostMetric,
    ScenarioCostParameters,
    StructureType,
    TableFuelConsumptionProvider,
    TripObservation,
    VesselCatalogue,
    VesselReference,
    assign_traffic,
)

M1 = VesselReference("M1", "I", 350.0, 5.05, 38.5, 2.2, 1.0)
NETWORK = AssignmentNetwork(
    (
        NetworkEdge("A|B", "A", "B", 10.0, 10.0, waterway_cemt_class="I"),
        NetworkEdge("B|D", "B", "D", 10.0, 10.0, waterway_cemt_class="I"),
        NetworkEdge("A|C", "A", "C", 15.0, 20.0, waterway_cemt_class="I"),
        NetworkEdge("C|D", "C", "D", 15.0, 20.0, waterway_cemt_class="I"),
    ),
    ("A", "B", "C", "D"),
    np.array([(5.0, 52.0), (5.1, 52.05), (5.1, 51.95), (5.2, 52.0)]),
    (None, None, None, None),
)
COST_INPUTS = CostInputs(
    (
        CostRates(
            "droge_bulk",
            "M1",
            10.0,
            7.0,
            2.0,
            10.0,
            1.0,
            0.5,
            4.0,
            5.0,
            6.0,
            2.0,
            3.0,
            "verification-case",
        ),
    ),
    TableFuelConsumptionProvider(
        [FuelConsumptionObservation("m1-i", "M1", "I", 40.0, 60.0, 80.0, "verification-case")],
        [M1],
    ),
    ScenarioCostParameters(
        fuel_price_eur_per_l=2.0,
        interest_rate_percent=4.0,
        cargo_waiting_time_h=2.0,
        idle_fuel_factor=0.1,
    ),
)
CATALOGUE = VesselCatalogue((M1,))


def _trips(*cargo_t: float) -> AssignmentTrips:
    trips = tuple(
        TripObservation(
            f"trip-{i}",
            "NLAAA",
            "NLDDD",
            None,
            350.0,
            cargo,
            None,
            "",
            "verification-case",
            vessel_rws_class="M1",
            appearance="droge_bulk",
            appearance_source="verification-case",
        )
        for i, cargo in enumerate(cargo_t)
    )
    count = len(trips)
    return AssignmentTrips(
        trips,
        gpd.GeoSeries([Point(4.99, 52.0)] * count, crs="EPSG:4326"),
        gpd.GeoSeries([Point(5.21, 52.0)] * count, crs="EPSG:4326"),
    )


def _route(result: Any, trip_index: int = 0) -> str:
    edges = result.trip_edges
    return "-".join(["A", *edges.loc[edges["voyage_index"] == trip_index, "to_node"]])


def metric_choice_actual() -> dict[str, Any]:
    trips = _trips(0.0)
    time = assign_traffic(
        trips,
        NETWORK,
        COST_INPUTS,
        CATALOGUE,
        settings=AssignmentSettings(metric=RouteCostMetric.TRAVEL_TIME),
    )
    cost = assign_traffic(trips, NETWORK, COST_INPUTS, CATALOGUE)
    return {
        "time_route": _route(time),
        "time_route_weight_h": time.trips.loc[0, "route_weight"],
        "cost_route": _route(cost),
        "cost_route_weight_eur": cost.trips.loc[0, "route_weight"],
    }


def reported_cost_actual() -> dict[str, Any]:
    trip_row = assign_traffic(_trips(175.0), NETWORK, COST_INPUTS, CATALOGUE).trips.iloc[0]
    return {
        "fuel_l": trip_row["fuel_l"],
        "fixed_cost_eur": trip_row["fixed_cost_eur"],
        "reported_total_cost_eur": trip_row["reported_total_cost_eur"],
    }


def penalty_actual() -> dict[str, Any]:
    lock = ArcRestriction("C|D", "DELOCK", StructureType.LOCK, "verification-case", max_beam_m=4.55)
    closed = ArcRestriction(
        "B|D", "NLLOCK", StructureType.LOCK, "verification-case", max_beam_m=4.0
    )
    trip_row = assign_traffic(
        _trips(0.0),
        NETWORK,
        COST_INPUTS,
        CATALOGUE,
        [lock, closed],
        settings=AssignmentSettings(metric=RouteCostMetric.TRAVEL_TIME),
    ).trips.iloc[0]
    return {
        "penalty_total_h": trip_row["penalty_total_h"],
        "choice_time_h": trip_row["choice_time_h"],
        "reported_time_h": trip_row["reported_time_h"],
    }


def rounded_load_actual() -> dict[str, Any]:
    trip_table = assign_traffic(
        _trips(170.0),
        NETWORK,
        COST_INPUTS,
        CATALOGUE,
        settings=AssignmentSettings(routing_ratio_step=0.5),
    ).trips
    return {
        "routing_loading_ratio": trip_table.loc[0, "routing_loading_ratio"],
        "fuel_l": trip_table.loc[0, "fuel_l"],
        "reported_total_cost_eur": trip_table.loc[0, "reported_total_cost_eur"],
    }
