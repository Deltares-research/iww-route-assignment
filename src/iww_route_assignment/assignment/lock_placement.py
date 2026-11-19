"""Place lock complexes on network edges, so a vessel's side and position are known from data.

A lock sits on exactly one edge. The edge comes from the section link of the lock
(``edges.geoparquet``: section to lock). When several edges share the section code, the edge
closest to the lock point is used. ``fraction`` is the position along the edge, 0 at
``from_node`` and 1 at ``to_node``. A vessel that sails from ``from_node`` to ``to_node``
enters from side A, and the other way round from side B.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import shapely
from shapely import LineString

from ..domain.locks import LockComplex
from .network import AssignmentNetwork
from .snapping import NETWORK_CRS, SNAP_CRS

SIDE_A = "A"
SIDE_B = "B"


@dataclass(frozen=True, slots=True)
class LockPlacement:
    """One lock complex on one edge."""

    lock_id: str
    edge_index: int
    fraction: float
    distance_m: float | None = None


@dataclass(frozen=True, slots=True, eq=False)
class LockPlacements:
    """Placements, the placements per edge in order from ``from_node``, and a report."""

    placements: tuple[LockPlacement, ...]
    by_edge: Mapping[int, tuple[LockPlacement, ...]]
    report: Mapping[str, Any]

    def on_edge(self, edge_index: int, *, forward: bool) -> tuple[LockPlacement, ...]:
        """Locks on an edge in the order a vessel meets them."""
        found = self.by_edge.get(edge_index, ())
        return found if forward else found[::-1]


def entry_side(*, forward: bool) -> str:
    """Side a vessel enters from: A when sailing from ``from_node`` to ``to_node``."""
    return SIDE_A if forward else SIDE_B


def _edge_line(network: AssignmentNetwork, index: int) -> LineString:
    line = network.edge_lines[index]
    if line is not None and not line.is_empty:
        return line
    return LineString(
        [
            network.node_lon_lat[network.edge_from[index]],
            network.node_lon_lat[network.edge_to[index]],
        ]
    )


def place_locks(
    network: AssignmentNetwork,
    complexes: Mapping[str, LockComplex],
    section_locks: Mapping[str, Sequence[str]],
    *,
    max_distance_m: float | None = None,
) -> LockPlacements:
    """Place every lock complex that has chambers on one edge of the network.

    ``section_locks`` maps a section code to its lock ids. A lock without a linked edge, or
    further than ``max_distance_m`` from its edge, is not placed and is counted in the report.
    """
    edges_by_code: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(network.edges):
        if edge.section_code:
            edges_by_code[edge.section_code].append(index)
    candidates: dict[str, list[int]] = defaultdict(list)
    for code, lock_ids in section_locks.items():
        for lock_id in lock_ids:
            if lock_id in complexes:
                candidates[lock_id].extend(edges_by_code.get(code, ()))

    counts: Counter[str] = Counter()
    pairs: list[tuple[str, int]] = []
    for lock_id in complexes:
        found = sorted(set(candidates.get(lock_id, ())))
        if not found:
            counts["locks_without_edge"] += 1
            continue
        pairs.extend((lock_id, index) for index in found)

    chosen: dict[str, LockPlacement] = {}
    if pairs:
        chosen = _choose(network, complexes, pairs, counts)
    placements: list[LockPlacement] = []
    for placement in chosen.values():
        if (
            max_distance_m is not None
            and placement.distance_m is not None
            and placement.distance_m > max_distance_m
        ):
            counts["locks_too_far_from_edge"] += 1
            continue
        placements.append(placement)

    grouped: dict[int, list[LockPlacement]] = defaultdict(list)
    for placement in placements:
        grouped[placement.edge_index].append(placement)
    by_edge = {
        edge: tuple(sorted(items, key=lambda p: (p.fraction, p.lock_id)))
        for edge, items in grouped.items()
    }
    report = {
        "complexes": len(complexes),
        "placed": len(placements),
        "edges_with_locks": len(by_edge),
        "edges_with_several_locks": sum(1 for items in by_edge.values() if len(items) > 1),
        **dict(sorted(counts.items())),
    }
    return LockPlacements(tuple(placements), MappingProxyType(by_edge), MappingProxyType(report))


def _choose(
    network: AssignmentNetwork,
    complexes: Mapping[str, LockComplex],
    pairs: list[tuple[str, int]],
    counts: Counter[str],
) -> dict[str, LockPlacement]:
    """Pick the closest candidate edge per lock and the position along it."""
    lock_ids = [lock_id for lock_id, _ in pairs]
    edge_ids = np.array([index for _, index in pairs], dtype=np.int64)
    lines = gpd.GeoSeries([_edge_line(network, i) for i in edge_ids], crs=NETWORK_CRS)
    lines = lines.to_crs(SNAP_CRS).to_numpy()
    ends = gpd.GeoSeries(
        gpd.points_from_xy(
            network.node_lon_lat[network.edge_from[edge_ids], 0],
            network.node_lon_lat[network.edge_from[edge_ids], 1],
        ),
        crs=NETWORK_CRS,
    ).to_crs(SNAP_CRS)
    start_of_edge = shapely.get_point(lines, 0)
    from_point = ends.to_numpy()
    has_point = np.array([complexes[lock_id].lon is not None for lock_id in lock_ids])
    lon = np.array([complexes[i].lon if complexes[i].lon is not None else 0.0 for i in lock_ids])
    lat = np.array([complexes[i].lat if complexes[i].lat is not None else 0.0 for i in lock_ids])
    points = (
        gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs=NETWORK_CRS).to_crs(SNAP_CRS).to_numpy()
    )

    distance: npt.NDArray[np.float64] = np.where(has_point, shapely.distance(lines, points), np.nan)
    fraction: npt.NDArray[np.float64] = np.where(
        has_point, shapely.line_locate_point(lines, points, normalized=True), 0.5
    )
    # The line may run from to_node to from_node; fractions refer to from_node -> to_node.
    reversed_line = shapely.distance(start_of_edge, from_point) > shapely.distance(
        shapely.get_point(lines, -1), from_point
    )
    fraction = np.where(reversed_line, 1.0 - fraction, fraction)

    best: dict[str, int] = {}
    for row, lock_id in enumerate(lock_ids):
        current = best.get(lock_id)
        if current is None:
            best[lock_id] = row
        elif has_point[row] and (not has_point[current] or distance[row] < distance[current]):
            best[lock_id] = row
    candidates_per_lock = Counter(lock_ids)
    result = {}
    for lock_id, row in best.items():
        if not has_point[row]:
            counts["locks_without_point"] += 1
        if candidates_per_lock[lock_id] > 1:
            counts["locks_on_shared_section_code"] += 1
        result[lock_id] = LockPlacement(
            lock_id,
            int(edge_ids[row]),
            float(fraction[row]),
            float(distance[row]) if has_point[row] else None,
        )
    return result
