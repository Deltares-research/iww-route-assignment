"""An OpenTNSim vessel that sails a route over an assignment network at network speed.

OpenTNSim's own ``Movable.move`` looks up the geometry of every node of the graph for every
edge it passes, which is too slow for a national network. This vessel keeps the OpenTNSim
interface (``route``, ``position_on_route``, ``v``, ``distance_left_on_edge`` and the
``on_pass_edge_functions`` hooks, called with ``(origin, destination)`` before the edge is
sailed) and does the same work on arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import opentnsim.core as core
import simpy
from shapely import Point

from ..assignment.network import AssignmentNetwork
from .locks import LockPassage

if TYPE_CHECKING:
    from .rerouting import Rerouter


@dataclass(frozen=True, slots=True, eq=False)
class RouteData:
    """Per-edge numbers shared by all vessels of a simulation."""

    network: AssignmentNetwork
    length_m: npt.NDArray[np.float64]
    speed_m_s: npt.NDArray[np.float64]
    has_lock: npt.NDArray[np.bool_]

    @classmethod
    def from_network(
        cls, network: AssignmentNetwork, lock_edges: npt.NDArray[np.int64]
    ) -> RouteData:
        has_lock = np.zeros(network.edge_count, dtype=bool)
        has_lock[lock_edges] = True
        return cls(
            network,
            network.array("distance_km") * 1000.0,
            network.array("speed_km_per_h") / 3.6,
            has_lock,
        )


class RouteVessel(core.Movable):
    """A vessel with a length and beam that sails ``nodes`` (positions in the network)."""

    position_on_route: int

    def __init__(
        self,
        *,
        env: simpy.Environment,
        data: RouteData,
        voyage_index: int,
        name: str,
        length_m: float,
        beam_m: float,
        nodes: list[int],
        trip_row: Any = None,
        rerouter: Rerouter | None = None,
    ) -> None:
        network = data.network
        self.data = data
        self.nodes: list[int] = list(nodes)
        self.edges: list[int] = network.path_edges(self.nodes)
        lon, lat = network.node_lon_lat[self.nodes[0]]
        super().__init__(
            env=env,
            v=1.0,
            geometry=Point(float(lon), float(lat)),
            route=[network.node_ids[i] for i in self.nodes],
        )
        self.name = name
        self.voyage_index = voyage_index
        self.L = length_m
        self.B = beam_m
        self.trip_row = trip_row
        self.rerouter = rerouter
        self.lock_passages: list[LockPassage] = []
        self.finished_s: float | None = None
        self.lock_positions: list[int] = []
        self._update_lock_positions()

    def _check_attributes(self) -> None:
        """Skip the node geometry scan of OpenTNSim: the network guarantees it."""

    def _update_lock_positions(self) -> None:
        mask = self.data.has_lock
        self.lock_positions = [k for k, edge in enumerate(self.edges) if mask[edge]]

    def set_route(self, nodes: list[int]) -> None:
        """Replace the route. The vessel must be at ``nodes[position_on_route]``."""
        network = self.data.network
        self.nodes = list(nodes)
        self.edges = network.path_edges(self.nodes)
        self.route = [network.node_ids[i] for i in self.nodes]
        self._update_lock_positions()

    def move(self) -> Any:
        """Sail the route edge by edge. Yields simulation time, like ``Movable.move``."""
        data = self.data
        ids = data.network.node_ids
        while self.position_on_route < len(self.edges):
            if (
                self.rerouter is not None
                and self.lock_positions
                and self.lock_positions[-1] >= self.position_on_route
            ):
                self.rerouter.consider(self)
            position = self.position_on_route
            edge = self.edges[position]
            origin, destination = ids[self.nodes[position]], ids[self.nodes[position + 1]]
            self.distance_left_on_edge = float(data.length_m[edge])
            self.v = float(data.speed_m_s[edge])
            for on_pass_edge in self.on_pass_edge_functions:
                yield from on_pass_edge(origin, destination)
            yield self.env.timeout(self.distance_left_on_edge / self.v)
            self.distance += self.distance_left_on_edge
            self.position_on_route = position + 1
            for on_complete in self.on_complete_pass_edge_functions:
                yield from on_complete(destination)
        self.finished_s = float(self.env.now)
