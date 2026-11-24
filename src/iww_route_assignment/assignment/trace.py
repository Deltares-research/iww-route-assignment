"""Follow one trip section by section, with every cost part, for checking by hand."""

from __future__ import annotations

import pandas as pd

from ..exceptions import ValidationError
from .runner import AssignmentResult

TRACE_COLUMNS = (
    "seq",
    "arc_id",
    "section_code",
    "name",
    "cemt_class",
    "from_node",
    "to_node",
    "distance_km",
    "speed_km_per_h",
    "lock_count",
    "lock_time_h",
    "reported_time_h",
    "fuel_rate_l_per_km",
    "fuel_l",
    "fuel_method",
    "fuel_confidence",
    "distance_cost_eur",
    "time_cost_eur",
    "fuel_cost_eur",
    "reported_cost_eur",
    "penalty_total_h",
    "choice_cost_eur",
    "cumulative_km",
    "cumulative_reported_cost_eur",
)


def trace_trip(result: AssignmentResult, trip_id: str) -> pd.DataFrame:
    """Return the sailed sections of one trip in order, with running totals.

    Each row holds what is needed to recompute its cost on paper: distance, speed, lock time,
    the fuel rate with its method, and the three cost parts that add up to the reported cost.
    """
    edges = result.trip_edges[result.trip_edges["trip_id"] == trip_id]
    if edges.empty:
        known = result.trips["trip_id"].eq(trip_id).any()
        reason = "it was not routed" if known else "no such trip"
        raise ValidationError(f"No trace for trip {trip_id!r}: {reason}")
    names = result.intensity.set_index("edge_index")[["section_code", "name", "cemt_class"]]
    trace = edges.sort_values("seq").join(names, on="edge_index")
    trace["cumulative_km"] = trace["distance_km"].cumsum()
    trace["cumulative_reported_cost_eur"] = trace["reported_cost_eur"].cumsum()
    return trace[list(TRACE_COLUMNS)].reset_index(drop=True)
