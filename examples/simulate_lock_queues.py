"""Simulate lock queues for twelve synthetic trips. No data files needed.

It builds on ``assign_synthetic_trips.py``: two routes lead from A to D and by cost the
vessels take A-B-D, which has one lock between B and D. The lock has one chamber that takes
one vessel per crossing and needs one hour to switch sides, so a queue builds up.

Each vessel leaves A every half hour from 08:00. The assignment first finds the free-flow
route. The simulation then replays the trips in time. A vessel leaving A looks at the
expected wait at the lock and, when the wait is long enough and A-C-D is clearly faster
overall, takes that route instead.

Change a setting below and run again: ``REROUTE = False`` shows the full queue,
``REROUTE_THRESHOLD_H`` sets how much faster the other route must be before a vessel switches.

Needs the simulation extra: ``pip install "iww-route-assignment[simulation]"``.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import Point

sys.path.insert(0, str(Path(__file__).parent))

from assign_synthetic_trips import (  # noqa: E402
    example_cost_inputs,
    example_graph,
    example_structures,
    example_trips,
)

from iww_route_assignment import (  # noqa: E402
    AssignmentSettings,
    AssignmentTrips,
    RouteCostMetric,
    ScenarioCostParameters,
    assign_traffic,
)
from iww_route_assignment.assignment import place_locks  # noqa: E402
from iww_route_assignment.domain import ChamberSpec, LockComplex  # noqa: E402
from iww_route_assignment.io import build_euris_inputs  # noqa: E402
from iww_route_assignment.simulation import LockSimulationSettings, LockSimulator  # noqa: E402

# Settings ---------------------------------------------------------------------------------
METRIC = RouteCostMetric.TRAVEL_COST_EUR
REROUTE = True
REROUTE_THRESHOLD_H = 0.5
OPERATION_TIME_MIN = 60.0  # a chamber crossing, from one side to the other
TRIP_COUNT = 12
HEADWAY_MIN = 30.0
FIRST_DEPARTURE = datetime(2026, 1, 5, 8, 0)
SCENARIO = ScenarioCostParameters(
    fuel_price_eur_per_l=1.20, interest_rate_percent=3.0, cargo_waiting_time_h=1.0
)
# -----------------------------------------------------------------------------------------


def departing_trips(count: int) -> AssignmentTrips:
    """The ``empty`` trip of the assignment example, repeated and given a departure time."""
    template = example_trips().trips[0]
    trips = tuple(
        dataclasses.replace(
            template,
            source_row_id=f"trip-{i:02d}",
            departure_time=FIRST_DEPARTURE + timedelta(minutes=HEADWAY_MIN * i),
        )
        for i in range(count)
    )
    return AssignmentTrips(
        trips,
        gpd.GeoSeries([Point(4.99, 52.0)] * count, crs="EPSG:4326"),
        gpd.GeoSeries([Point(5.61, 52.0)] * count, crs="EPSG:4326"),
    )


def main() -> None:
    structures = example_structures(lock_width_m=12.0)
    euris = build_euris_inputs(example_graph(), structures)
    cost_inputs, catalogue = example_cost_inputs(SCENARIO)

    # The lock complex of section NL02 (B-D): one chamber of 200 x 12 m would take several
    # vessels, so use a short one that takes a single M8 (110 x 11.4 m) per crossing.
    chamber = ChamberSpec(
        "c1", length_m=120.0, width_m=12.0, operation_time_s=OPERATION_TIME_MIN * 60
    )
    complexes = {"NLLOCK1": LockComplex("NLLOCK1", "Example lock", (chamber,), 5.45, 52.05)}
    placements = place_locks(euris.network, complexes, structures.section_locks)
    simulator = LockSimulator(
        complexes,
        placements,
        LockSimulationSettings(reroute=REROUTE, reroute_threshold_h=REROUTE_THRESHOLD_H),
    )

    result = assign_traffic(
        departing_trips(TRIP_COUNT),
        euris.network,
        cost_inputs,
        catalogue,
        euris.restrictions,
        settings=AssignmentSettings(metric=METRIC, points_crs="EPSG:4326"),
        simulator=simulator,
    )

    routes = result.trip_edges.groupby("trip_id")["to_node"].agg(lambda s: "A-" + "-".join(s))
    table = (
        result.trips.set_index("trip_id")[
            ["departure_time", "reported_time_h", "lock_waiting_h", "n_reroutes"]
        ]
        .join(routes.rename("route"))
        .assign(departure_time=lambda t: t["departure_time"].dt.strftime("%H:%M"))
    )
    with pd.option_context(
        "display.width", 200, "display.max_columns", None, "display.float_format", "{:,.2f}".format
    ):
        print(f"Route search minimises {METRIC.value}; rerouting {'on' if REROUTE else 'off'}\n")
        print(table)
        print("\nLock summary")
        print(result.tables["lock_summary"].drop(columns=["lock_id"]).to_string(index=False))


if __name__ == "__main__":
    main()
