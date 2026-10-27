"""Assign voyages to the network and price every route with the cost function.

:func:`assign_traffic` works on objects in memory and reads no files. Use
:mod:`iww_route_assignment.io.euris` and :mod:`iww_route_assignment.io.assignment` to load
inputs and write outputs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import LineString

from ..domain.models import TripRejection
from ..domain.navigability import ArcRestriction
from ..navigability import RestrictionIndex, build_restriction_index
from .network import AssignmentNetwork
from .pricing import CostCoverageReport, CostInputs, EdgeTravel
from .router import EDGE_COLUMNS, PENALTY_COLUMNS, RoutingContext, RoutingOutput, route_voyages
from .settings import AssignmentSettings, NavigabilityPenalties, RoutePreferences
from .snapping import NETWORK_CRS, build_snap_index
from .voyages import VOYAGE_PENDING, AssignmentTrips, VesselCatalogue, prepare_voyages

logger = logging.getLogger(__name__)

PENALISED_KEY_COLUMNS = (
    "edge_index",
    "penalty_type",
    "structure_id",
    "structure_type",
    "limit_m",
    "penalty_h",
    "source",
)


@dataclass(frozen=True, slots=True, eq=False)
class AssignmentResult:
    """Tables produced by :func:`assign_traffic`.

    ``voyages`` has one row per valid trip. ``rejections`` lists source rows that were not
    valid trips, with a reason. ``voyage_edges`` has one row per traversed edge of a
    successful voyage. ``intensity`` sums traffic per used edge. ``penalised`` lists every
    edge and limit that added a penalty, with the trips that met it.
    """

    voyages: pd.DataFrame
    voyage_edges: pd.DataFrame
    intensity: gpd.GeoDataFrame
    penalised: gpd.GeoDataFrame
    coverage: CostCoverageReport
    summary: Mapping[str, Any]
    rejections: tuple[TripRejection, ...] = ()


def edge_travel(network: AssignmentNetwork) -> EdgeTravel:
    """Travel inputs per edge. Lock passage time is reported time, not a penalty."""
    return EdgeTravel(
        network.array("distance_km"),
        network.array("speed_km_per_h"),
        network.array("lock_service_time_h"),
    )


def edge_table(network: AssignmentNetwork) -> gpd.GeoDataFrame:
    """One row per network edge, in edge order."""
    lines = [
        line
        if line is not None and not line.is_empty
        else LineString(
            [network.node_lon_lat[network.edge_from[i]], network.node_lon_lat[network.edge_to[i]]]
        )
        for i, line in enumerate(network.edge_lines)
    ]
    return gpd.GeoDataFrame(
        {
            "edge_index": np.arange(network.edge_count),
            "arc_id": [e.arc_id for e in network.edges],
            "section_code": [e.section_code for e in network.edges],
            "name": [e.name for e in network.edges],
            "cemt_class": [e.waterway_cemt_class or "" for e in network.edges],
            "length_km": network.array("distance_km"),
            "lock_count": [e.lock_count for e in network.edges],
        },
        geometry=lines,
        crs=NETWORK_CRS,
    )


def _voyage_edges(
    output: RoutingOutput, voyages: pd.DataFrame, network: AssignmentNetwork
) -> pd.DataFrame:
    columns = {
        name: (np.concatenate(parts) if parts else np.array([], dtype=object))
        for name, parts in output.edges.items()
    }
    frame = pd.DataFrame(columns, columns=list(EDGE_COLUMNS))
    info = voyages.set_index("voyage_index")[
        ["trip_id", "vessel_rws_class", "cargo_t", "is_loaded"]
    ]
    frame = frame.join(info, on="voyage_index")
    node_ids = np.asarray(network.node_ids, dtype=object)
    frame["from_node"] = node_ids[frame["from_node"].to_numpy(dtype=np.int64)]
    frame["to_node"] = node_ids[frame["to_node"].to_numpy(dtype=np.int64)]
    arc_ids = np.asarray([e.arc_id for e in network.edges], dtype=object)
    frame.insert(3, "arc_id", arc_ids[frame["edge_index"].to_numpy(dtype=np.int64)])
    lead = ["voyage_index", "trip_id", "seq", "edge_index", "arc_id", "from_node", "to_node"]
    rest = [c for c in frame.columns if c not in lead]
    return frame[lead + rest].sort_values(["voyage_index", "seq"], ignore_index=True)


def _intensity(voyage_edges: pd.DataFrame, edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    summed = (
        voyage_edges.assign(empty=~voyage_edges["is_loaded"].astype(bool))
        .groupby("edge_index")
        .agg(
            trip_count=("voyage_index", "size"),
            trip_count_empty=("empty", "sum"),
            cargo_t=("cargo_t", "sum"),
            **{name: (name, "sum") for name in (*PENALTY_COLUMNS, "penalty_total_h")},
            reported_time_h=("reported_time_h", "sum"),
            reported_cost_eur=("reported_cost_eur", "sum"),
            choice_cost_eur=("choice_cost_eur", "sum"),
            fuel_l=("fuel_l", "sum"),
        )
    )
    return edges.merge(summed, left_on="edge_index", right_index=True, how="inner")


def _penalised(output: RoutingOutput, edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows = [
        dict(zip(PENALISED_KEY_COLUMNS, key, strict=True))
        | {"trips": trips, "cargo_t": cargo, "vessel_classes": ",".join(sorted(classes))}
        for key, (trips, cargo, classes) in sorted(
            output.penalised.items(), key=lambda item: tuple(map(str, item[0]))
        )
    ]
    columns = [*PENALISED_KEY_COLUMNS, "trips", "cargo_t", "vessel_classes"]
    table = pd.DataFrame(rows, columns=columns)
    meta = edges[["edge_index", "arc_id", "section_code", "name", "geometry"]]
    return gpd.GeoDataFrame(
        table.merge(meta, on="edge_index"), geometry="geometry", crs=NETWORK_CRS
    )


def assign_traffic(
    trips: AssignmentTrips,
    network: AssignmentNetwork,
    cost_inputs: CostInputs,
    catalogue: VesselCatalogue,
    restrictions: Iterable[ArcRestriction] | RestrictionIndex = (),
    *,
    settings: AssignmentSettings | None = None,
    penalties: NavigabilityPenalties | None = None,
    preferences: RoutePreferences | None = None,
) -> AssignmentResult:
    """Route every voyage and report its time, fuel and cost from the cost function.

    1. Snap origin and destination to the network and check vessel and cost inputs.
    2. Group voyages by routing key and search once per key and origin node, minimising
       ``settings.metric``. Penalties from limits and preferences steer the search.
    3. Reprice each chosen route with the exact load. Fixed cost is added once per trip.
    4. Sum traffic, cost and penalties per edge.
    """
    settings = settings or AssignmentSettings()
    penalties = penalties or NavigabilityPenalties()
    preferences = preferences or RoutePreferences()
    # A plain dict, so the index can be sent to worker processes.
    index = dict(
        restrictions if isinstance(restrictions, Mapping) else build_restriction_index(restrictions)
    )
    unknown_arcs = set(index) - {edge.arc_id for edge in network.edges}
    if unknown_arcs:
        logger.warning("%d restriction arcs are not in the network", len(unknown_arcs))

    prepared = prepare_voyages(
        trips, network, build_snap_index(network), catalogue, cost_inputs, settings
    )
    table = prepared.table
    pending = table[table["status"] == VOYAGE_PENDING]
    logger.info("Routing %d of %d voyages", len(pending), len(table))
    context = RoutingContext(
        network,
        edge_travel(network),
        index,
        catalogue,
        cost_inputs,
        settings,
        penalties,
        preferences,
    )
    output = route_voyages(context, pending)

    routed = pd.DataFrame.from_records(output.voyages)
    if routed.empty:
        routed = pd.DataFrame(columns=["voyage_index", "status", "detail", "route_metric"])
    voyages = table.drop(columns=["routing_key"]).set_index("voyage_index")
    routed = routed.set_index("voyage_index")
    voyages.update(routed[["status", "detail"]])
    voyages = voyages.join(routed.drop(columns=["status", "detail"]), how="left")
    voyages["route_metric"] = settings.metric.value
    voyages = voyages.reset_index()

    edges = edge_table(network)
    voyage_edges = _voyage_edges(output, voyages, network)
    status_counts = voyages["status"].value_counts().sort_index()
    summary = {
        "voyages": len(voyages),
        "status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "route_metric": settings.metric.value,
        "network_edges": network.edge_count,
        "network_nodes": len(network.node_ids),
        "network_components": network.component_count(),
        "restriction_arcs": len(index),
        "missing_vessel_classes": list(prepared.coverage.missing_vessels),
        "missing_cost_rates": [list(pair) for pair in prepared.coverage.missing_rates],
        "trip_rejections": len(trips.rejections),
        "excluded_duplicate_rows": trips.excluded_duplicate_rows,
    }
    logger.info("Assignment finished: %s", summary["status_counts"])
    return AssignmentResult(
        voyages,
        voyage_edges,
        _intensity(voyage_edges, edges),
        _penalised(output, edges),
        prepared.coverage,
        MappingProxyType(summary),
        trips.rejections,
    )
