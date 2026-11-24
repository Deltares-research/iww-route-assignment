"""Snap trip points to the network: nearest edge first, then the nearer of its two nodes."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import shapely
from shapely import LineString, STRtree

from ..exceptions import ValidationError
from .network import AssignmentNetwork

SNAP_CRS = "EPSG:3035"
"""Equal-area metric CRS for Europe, used for every snapping distance."""

NETWORK_CRS = "EPSG:4326"


@dataclass(frozen=True, slots=True, eq=False)
class EdgeSnapIndex:
    """Spatial index of network edges in ``SNAP_CRS`` metres, in edge order."""

    tree: STRtree
    from_xy: npt.NDArray[np.float64]
    to_xy: npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True, eq=False)
class SnapResult:
    """Node position, snapped edge and distances in metres. -1 and NaN mean no point."""

    node: npt.NDArray[np.int64]
    edge: npt.NDArray[np.int64]
    edge_distance_m: npt.NDArray[np.float64]
    node_distance_m: npt.NDArray[np.float64]


def build_snap_index(network: AssignmentNetwork) -> EdgeSnapIndex:
    """Index edge lines. An edge without a line is indexed as a straight line."""
    nodes = gpd.GeoSeries(
        gpd.points_from_xy(network.node_lon_lat[:, 0], network.node_lon_lat[:, 1]),
        crs=NETWORK_CRS,
    ).to_crs(SNAP_CRS)
    node_xy = np.column_stack([nodes.x.to_numpy(), nodes.y.to_numpy()])
    lines = gpd.GeoSeries(list(network.edge_lines), crs=NETWORK_CRS).to_crs(SNAP_CRS).to_numpy()
    for index, line in enumerate(lines):
        if line is None or line.is_empty:
            lines[index] = LineString(
                [node_xy[network.edge_from[index]], node_xy[network.edge_to[index]]]
            )
    return EdgeSnapIndex(STRtree(lines), node_xy[network.edge_from], node_xy[network.edge_to])


def to_snap_crs(points: gpd.GeoSeries) -> gpd.GeoSeries:
    """Reproject points to ``SNAP_CRS``. Points must carry a CRS."""
    if points.crs is None:
        raise ValidationError("Trip points need a CRS before snapping")
    return points.to_crs(SNAP_CRS)


def snap_points(
    points: gpd.GeoSeries, index: EdgeSnapIndex, network: AssignmentNetwork
) -> SnapResult:
    """Snap each point to its nearest edge, then to the nearer node of that edge."""
    projected = to_snap_crs(points)
    size = len(projected)
    nodes = np.full(size, -1, dtype=np.int64)
    edges = np.full(size, -1, dtype=np.int64)
    edge_distance = np.full(size, np.nan)
    node_distance = np.full(size, np.nan)
    valid = (projected.notna() & ~projected.is_empty).to_numpy()
    if valid.any():
        geoms = projected[valid].to_numpy()
        (point_index, edge_index), distance = index.tree.query_nearest(
            geoms, return_distance=True, all_matches=False
        )
        xy = np.column_stack([shapely.get_x(geoms), shapely.get_y(geoms)])[point_index]
        d_from = np.hypot(*(xy - index.from_xy[edge_index]).T)
        d_to = np.hypot(*(xy - index.to_xy[edge_index]).T)
        use_from = d_from <= d_to
        rows = np.flatnonzero(valid)[point_index]
        nodes[rows] = np.where(use_from, network.edge_from[edge_index], network.edge_to[edge_index])
        edges[rows] = edge_index
        edge_distance[rows] = distance
        node_distance[rows] = np.where(use_from, d_from, d_to)
    return SnapResult(nodes, edges, edge_distance, node_distance)
