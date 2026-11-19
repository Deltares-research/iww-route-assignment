"""Look at one trip: the route the assignment planned and the route the vessel sailed."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from ..assignment import AssignmentNetwork, AssignmentResult, edge_table, edge_travel
from ..exceptions import ValidationError


def inspect_trip(
    result: AssignmentResult, network: AssignmentNetwork, trip_id: str
) -> gpd.GeoDataFrame:
    """Planned and sailed edges of one simulated trip, with geometry for plotting.

    One row per edge and route. ``route`` is ``planned`` (free-flow assignment) or ``sailed``.
    ``shared`` is True when the edge is on both routes, so ``shared == False`` shows the
    detour. ``free_flow_time_h`` (travel plus fixed lock time) and ``penalty_h`` per edge show
    why the assignment chose the planned route: sum them per ``route``. Use
    ``.explore(column="route")`` in a notebook to see them on a map. A trip that was not
    simulated has no planned route here: its sailed route is its free-flow route.
    """
    planned = result.tables.get("planned_edges")
    if planned is None:
        raise ValidationError("The result has no simulation tables; run with a simulator")
    sailed = result.trip_edges[result.trip_edges["trip_id"] == trip_id]
    planned = planned[planned["trip_id"] == trip_id]
    if sailed.empty:
        raise ValidationError(f"Trip {trip_id!r} has no route in the result")
    columns = ["seq", "edge_index", "arc_id", "from_node", "to_node"]
    extra = [c for c in ("reported_time_h", "lock_waiting_h", "lock_service_h") if c in sailed]
    frames = [
        planned[[*columns, "penalty_h"]].assign(route="planned"),
        sailed[[*columns, *extra]]
        .rename(columns={"penalty_total_h": "penalty_h"})
        .assign(penalty_h=sailed["penalty_total_h"].to_numpy(), route="sailed"),
    ]
    both = pd.concat(frames, ignore_index=True)
    travel = edge_travel(network)
    edge = both["edge_index"].to_numpy(dtype=int)
    both["free_flow_time_h"] = (
        travel.distance_km[edge] / travel.speed_km_per_h[edge] + travel.reported_fixed_time_h[edge]
    )
    both["lock_count"] = [network.edges[i].lock_count for i in edge]
    on_planned = set(planned["edge_index"])
    on_sailed = set(sailed["edge_index"])
    both["shared"] = both["edge_index"].isin(on_planned & on_sailed)
    geometry = edge_table(network).set_index("edge_index")["geometry"]
    return gpd.GeoDataFrame(
        both, geometry=geometry.loc[both["edge_index"]].to_numpy(), crs="EPSG:4326"
    )
