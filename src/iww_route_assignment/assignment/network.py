"""Routing network contract: arcs with travel inputs, nodes with coordinates.

The network is undirected. Every edge can be sailed both ways with the same travel inputs.
A file adapter such as :mod:`iww_route_assignment.io.euris` builds it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from math import isfinite

import numpy as np
import numpy.typing as npt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from shapely import LineString

from ..domain.classifications import normalise_cemt_class
from ..exceptions import ValidationError

MIN_SEARCH_WEIGHT = 1e-12
"""Search weights below this are raised to it, so zero-length arcs stay in the graph."""


@dataclass(frozen=True, slots=True)
class NetworkEdge:
    """One undirected arc with the inputs the cost function needs."""

    arc_id: str
    from_node: str
    to_node: str
    distance_km: float
    speed_km_per_h: float
    section_code: str = ""
    name: str = ""
    water_name: str = ""
    waterway_cemt_class: str | None = None
    lock_service_time_h: float = 0.0
    lock_count: int = 0

    def __post_init__(self) -> None:
        for name in ("arc_id", "from_node", "to_node"):
            if not getattr(self, name).strip():
                raise ValidationError(f"{name} must not be empty")
        if not isfinite(self.distance_km) or self.distance_km < 0:
            raise ValidationError(f"distance_km must be finite and non-negative for {self.arc_id}")
        if not isfinite(self.speed_km_per_h) or self.speed_km_per_h <= 0:
            raise ValidationError(f"speed_km_per_h must be positive for {self.arc_id}")
        if not isfinite(self.lock_service_time_h) or self.lock_service_time_h < 0:
            raise ValidationError(f"lock_service_time_h must be non-negative for {self.arc_id}")
        if self.waterway_cemt_class is not None:
            object.__setattr__(
                self, "waterway_cemt_class", normalise_cemt_class(self.waterway_cemt_class)
            )


@dataclass(frozen=True, slots=True, eq=False)
class AssignmentNetwork:
    """Edges, node coordinates (longitude, latitude in EPSG:4326) and edge lines.

    ``edge_lines`` may hold ``None`` for an edge without geometry. Snapping then uses a
    straight line between its nodes.
    """

    edges: tuple[NetworkEdge, ...]
    node_ids: tuple[str, ...]
    node_lon_lat: npt.NDArray[np.float64]
    edge_lines: tuple[LineString | None, ...]
    source: str = "unspecified"
    node_position: dict[str, int] = field(init=False, repr=False)
    edge_from: npt.NDArray[np.int64] = field(init=False, repr=False)
    edge_to: npt.NDArray[np.int64] = field(init=False, repr=False)
    edge_by_pair: dict[tuple[int, int], int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.edges:
            raise ValidationError("The network has no edges")
        if len(self.edge_lines) != len(self.edges):
            raise ValidationError("edge_lines must align with edges")
        coordinates = np.asarray(self.node_lon_lat, dtype=np.float64)
        if coordinates.shape != (len(self.node_ids), 2) or not np.all(np.isfinite(coordinates)):
            raise ValidationError("node_lon_lat must hold finite (lon, lat) per node")
        object.__setattr__(self, "node_lon_lat", coordinates)
        position = {node: index for index, node in enumerate(self.node_ids)}
        if len(position) != len(self.node_ids):
            raise ValidationError("node_ids must be unique")
        arc_ids = [edge.arc_id for edge in self.edges]
        if len(set(arc_ids)) != len(arc_ids):
            raise ValidationError("arc_id must be unique per edge")
        try:
            edge_from = np.array([position[e.from_node] for e in self.edges], dtype=np.int64)
            edge_to = np.array([position[e.to_node] for e in self.edges], dtype=np.int64)
        except KeyError as error:
            raise ValidationError(f"Edge refers to unknown node {error.args[0]!r}") from error
        by_pair: dict[tuple[int, int], int] = {}
        for index, (u, v) in enumerate(zip(edge_from.tolist(), edge_to.tolist(), strict=True)):
            key = (min(u, v), max(u, v))
            if key in by_pair:
                raise ValidationError(
                    f"Parallel edges between {self.node_ids[u]!r} and {self.node_ids[v]!r}; "
                    "the network must be a simple graph"
                )
            by_pair[key] = index
        object.__setattr__(self, "node_position", position)
        object.__setattr__(self, "edge_from", edge_from)
        object.__setattr__(self, "edge_to", edge_to)
        object.__setattr__(self, "edge_by_pair", by_pair)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def array(self, name: str) -> npt.NDArray[np.float64]:
        """Return one numeric edge attribute as an array in edge order."""
        return np.fromiter(
            (float(getattr(edge, name)) for edge in self.edges),
            dtype=np.float64,
            count=len(self.edges),
        )

    def search_matrix(self, weights: npt.NDArray[np.float64]) -> csr_matrix:
        """Return a symmetric sparse matrix for shortest-path search."""
        if weights.shape != (self.edge_count,):
            raise ValidationError("weights must hold one value per edge")
        if not np.all(np.isfinite(weights)) or np.any(weights < 0):
            raise ValidationError("search weights must be finite and non-negative")
        data = np.maximum(weights, MIN_SEARCH_WEIGHT)
        size = len(self.node_ids)
        rows = np.concatenate([self.edge_from, self.edge_to])
        cols = np.concatenate([self.edge_to, self.edge_from])
        return csr_matrix((np.concatenate([data, data]), (rows, cols)), shape=(size, size))

    def path_edges(self, nodes: Sequence[int]) -> list[int]:
        """Return edge indices along a node path."""
        return [
            self.edge_by_pair[(min(u, v), max(u, v))]
            for u, v in zip(nodes[:-1], nodes[1:], strict=True)
        ]

    def component_count(self) -> int:
        """Number of connected parts. Voyages between parts have no path."""
        count, _ = connected_components(self.search_matrix(np.ones(self.edge_count)))
        return int(count)
