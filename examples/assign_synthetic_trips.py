"""Assign three synthetic trips and price them with the cost function. No data files needed.

Run in the IDE: change a setting below and press Run. Two routes lead from A to D:

- A-B-D: 50 km at 11 km/h plus one lock of 0.5 h, so 5.05 h;
- A-C-D: 80 km at 16.5 km/h, so 4.85 h.

By time, A-C-D wins. By cost, A-B-D wins, because 30 km less distance cost outweighs
0.2 h more time. Make the lock narrower than the vessel and the penalty sends it via C.

Every table below is made up for this example. Real runs read the shared reference tables.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd
from shapely import Point

from iww_route_assignment import (
    AssignmentSettings,
    AssignmentTrips,
    CostInputs,
    CostRates,
    FuelConsumptionObservation,
    NavigabilityPenalties,
    RouteCostMetric,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    TripObservation,
    VesselCatalogue,
    VesselReference,
    assign_traffic,
)
from iww_route_assignment.io import build_euris_inputs, build_euris_structures

# Settings ---------------------------------------------------------------------------------
METRIC = RouteCostMetric.TRAVEL_COST_EUR  # or RouteCostMetric.TRAVEL_TIME
LOCK_WIDTH_M = 12.0  # set below 11.4 m to make the lock on A-B-D too narrow for M8
BEAM_PENALTY_H = 1000.0
SCENARIO = ScenarioCostParameters(
    fuel_price_eur_per_l=1.20, interest_rate_percent=3.0, cargo_waiting_time_h=1.0
)
# -----------------------------------------------------------------------------------------

PLACEHOLDER = "made-up-example"


def example_graph() -> nx.Graph:
    """Four nodes in EPSG:4326 with EuRIS-style attributes: length in m, limits in cm."""
    graph = nx.Graph()
    for node, (lon, lat) in {
        "A": (5.0, 52.0),
        "B": (5.3, 52.1),
        "C": (5.3, 51.9),
        "D": (5.6, 52.0),
    }.items():
        graph.add_node(node, geometry=Point(lon, lat))
    for u, v, km, speed, code in (
        ("A", "B", 25, 11.0, "NL01"),
        ("B", "D", 25, 11.0, "NL02"),
        ("A", "C", 40, 16.5, "NL03"),
        ("C", "D", 40, 16.5, "NL04"),
    ):
        graph.add_edge(
            u,
            v,
            length_m=km * 1000,
            maxspeed_up=speed,
            code=code,
            cemt="Vb",
            mwidthcm=2280,
            mdraughtcm=400,
        )
    return graph


def example_structures(lock_width_m: float):  # type: ignore[no-untyped-def]
    """One lock on section NL02, between B and D."""
    return build_euris_structures(
        pd.DataFrame({"section_id": ["NL02"], "lock_id": ["NLLOCK1"], "bridge_id": [None]}),
        pd.DataFrame(
            {
                "lock_id": ["NLLOCK1"],
                "dim_gate_width": [lock_width_m],
                "dim_usable_length": [200.0],
                "mdraughtcm": [500.0],
            }
        ),
        pd.DataFrame(
            columns=[
                "bridge_id",
                "dim_gate_width",
                "dim_height",
                "cl_height",
                "mheightcmc",
                "operable",
            ]
        ),
    )


def example_cost_inputs(scenario: ScenarioCostParameters) -> tuple[CostInputs, VesselCatalogue]:
    m8 = VesselReference("M8", "Va", 2800.0, 11.4, 110.0, 3.5, 1.4)
    rates = tuple(
        CostRates(
            appearance=appearance,
            vessel_rws_class="M8",
            labour_eur_per_h=35.0,
            labour_waiting_eur_per_h=30.0,
            maintenance_eur_per_h=4.0,
            maintenance_eur_per_km=3.0,
            insurance_eur_per_h=2.0,
            interest_eur_per_percent_h=0.3,
            depreciation_eur_per_h=10.0,
            port_dues_eur_per_h=1.0,
            other_eur_per_h=3.0,
            loading_eur_per_t=1.5,
            unloading_eur_per_t=1.5,
            source=PLACEHOLDER,
        )
        for appearance in ("droge_bulk", "natte_bulk")
    )
    fuel = TableFuelConsumptionProvider(
        [FuelConsumptionObservation("m8-vb", "M8", "Vb", 90.0, 120.0, 150.0, PLACEHOLDER)],
        [m8],
    )
    return CostInputs(rates, fuel, scenario), VesselCatalogue((m8,))


def example_trips() -> AssignmentTrips:
    def trip(trip_id: str, cargo_t: float, appearance: str) -> TripObservation:
        return TripObservation(
            trip_id,
            "NLAAA",
            "NLDDD",
            None,
            2800.0,
            cargo_t,
            None,
            "",
            PLACEHOLDER,
            vessel_rws_class="M8",
            appearance=appearance,
            appearance_source=PLACEHOLDER,
        )

    trips = (
        trip("empty", 0.0, "droge_bulk"),
        trip("half", 1400.0, "droge_bulk"),
        trip("full", 2800.0, "natte_bulk"),
    )
    import geopandas as gpd

    return AssignmentTrips(
        trips,
        gpd.GeoSeries([Point(4.99, 52.0)] * 3, crs="EPSG:4326"),
        gpd.GeoSeries([Point(5.61, 52.0)] * 3, crs="EPSG:4326"),
    )


def main() -> None:
    euris = build_euris_inputs(example_graph(), example_structures(LOCK_WIDTH_M))
    cost_inputs, catalogue = example_cost_inputs(SCENARIO)
    result = assign_traffic(
        example_trips(),
        euris.network,
        cost_inputs,
        catalogue,
        euris.restrictions,
        settings=AssignmentSettings(metric=METRIC, points_crs="EPSG:4326"),
        penalties=NavigabilityPenalties(beam_h=BEAM_PENALTY_H),
    )
    routes = result.trip_edges.groupby("trip_id")["to_node"].agg(lambda s: "A-" + "-".join(s))
    table = result.trips.set_index("trip_id")[
        [
            "status",
            "distance_km",
            "reported_time_h",
            "fuel_l",
            "fixed_cost_eur",
            "reported_total_cost_eur",
            "total_cost_eur",
            "penalty_total_h",
        ]
    ].join(routes.rename("route"))
    with pd.option_context(
        "display.width", 200, "display.max_columns", None, "display.float_format", "{:,.1f}".format
    ):
        print(f"Route search minimises {METRIC.value}\n")
        print(table)


if __name__ == "__main__":
    main()
