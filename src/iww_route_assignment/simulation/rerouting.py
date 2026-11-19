"""Reroute a vessel around a lock with a long queue, looking forward only.

A vessel considers a reroute at a node where it has another way to go, when the locks ahead
on its route are expected to cost it more than ``threshold_h`` of waiting. It then searches
the cheapest route to its destination with the expected waiting time added to every lock
edge, never through a node it has already passed. It only switches when the alternative is
at least ``threshold_h`` faster in choice time (travel time plus penalty hours), so a small
gain never changes a route.

Two things keep the check cheap, and neither changes which route a vessel picks.

- **Corridor.** An alternative can only win if its free-flow time is at most the free-flow time
  of the current route plus the expected wait minus the threshold, because waits are never
  negative. By the triangle inequality, a node ``n`` can only lie on such a path when
  ``d(origin, n) + d(n, destination) - d(origin, current)`` is within that budget. The two
  distance vectors are free-flow times, computed once per vessel state and node and cached, so
  a check costs a few array operations. Only locks inside the corridor are asked for a wait,
  and the path search is limited to it. A junction with no alternative edge in the corridor
  needs no search.
- **Lookahead.** With ``lookahead_h`` a vessel only knows the queues of locks within that many
  hours of sailing: along its own route for the locks on it, and by shortest sailing time from
  its position for the others. ``None`` means it knows every queue.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from ..assignment.lock_placement import LockPlacements, entry_side
from ..assignment.network import MIN_SEARCH_WEIGHT
from ..assignment.pricing import EdgeTravel, search_weights
from ..assignment.router import RoutingCaches, RoutingContext, key_weights
from ..domain.enums import RouteCostMetric
from .locks import Locks

if TYPE_CHECKING:
    from .vessel import RouteVessel

SECONDS_PER_HOUR = 3600.0
RELATIVE_TOLERANCE = 1e-9
CORRIDOR_TOLERANCE_H = 1e-6
"""Slack on the corridor, so rounding never cuts an alternative that just qualifies."""
DISTANCE_CACHE_SIZE = 1024
"""Free-flow distance vectors kept (one per vessel state and node, about 50 kB each)."""

VesselState = tuple[str, bool]


@dataclass(slots=True, eq=False)
class KeyData:
    """Free-flow numbers per routing key: search weights and choice time per edge.

    ``rates``, ``scenario`` and ``fuel_rate_l_per_h`` are only set for the cost metric.
    """

    weights: npt.NDArray[np.float64]
    time_h: npt.NDArray[np.float64]
    penalty_h: npt.NDArray[np.float64]
    fuel_rate_l_per_h: npt.NDArray[np.float64] | None = None
    rates: Any = None
    scenario: Any = None


@dataclass(frozen=True, slots=True)
class DirectedCosts:
    """Search weight and extra expected waiting hours per edge, for each sailing direction."""

    weight_forward: npt.NDArray[np.float64]
    weight_backward: npt.NDArray[np.float64]
    wait_forward_h: npt.NDArray[np.float64]
    wait_backward_h: npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class RerouteEvent:
    """One reroute of one vessel. Times are simulation seconds."""

    voyage_index: int
    time_s: float
    node: str
    expected_wait_h: float
    saving_h: float
    old_edges: int
    new_edges: int


class Rerouter:
    """Decides on reroutes for the vessels of one simulation.

    ``lookahead_h`` limits the queues a vessel knows about (see the module docstring), and
    ``exhaustive`` switches the corridor off and searches the whole network, to validate that
    the corridor changes no decision. It is slow.
    """

    def __init__(
        self,
        context: RoutingContext,
        caches: RoutingCaches,
        locks: Locks,
        placements: LockPlacements,
        threshold_h: float,
        lookahead_h: float | None = None,
        *,
        exhaustive: bool = False,
    ) -> None:
        self.context = context
        self.caches = caches
        self.locks = locks
        self.placements = placements
        self.threshold_h = threshold_h
        self.lookahead_h = lookahead_h
        self.exhaustive = exhaustive
        network = context.network
        travel = context.travel
        size = len(network.node_ids)
        rows = np.concatenate([network.edge_from, network.edge_to])
        cols = np.concatenate([network.edge_to, network.edge_from])
        self.degree = np.bincount(rows, minlength=size)
        self.lock_edges = np.array(sorted(placements.by_edge), dtype=np.int64)
        self._lock_from = network.edge_from[self.lock_edges]
        self._lock_to = network.edge_to[self.lock_edges]
        # Sailing time per edge without penalties: what a vessel can "see" ahead of it.
        self._sail_h = travel.distance_km / travel.speed_km_per_h + travel.reported_fixed_time_h
        self._sail_matrix = network.search_matrix(self._sail_h) if lookahead_h is not None else None
        # Fixed CSR layout of the directed graph, so a search only fills in the weights.
        self._order = np.lexsort((cols, rows))
        self._indices = cols[self._order].astype(np.int32)
        self._arc_rows = rows[self._order].astype(np.int32)
        self._indptr = np.concatenate([[0], np.cumsum(self.degree)]).astype(np.int32)
        self._keys: dict[str, KeyData] = {}
        self._time_matrices: dict[VesselState, csr_matrix] = {}
        self._distances: OrderedDict[tuple[VesselState, int], npt.NDArray[np.float64]] = (
            OrderedDict()
        )
        self.events: list[RerouteEvent] = []

    def key_data(self, row: Any) -> KeyData:
        key = str(row.routing_key)
        found = self._keys.get(key)
        if found is not None:
            return found
        context, caches, travel = self.context, self.caches, self.context.travel
        vessel, is_loaded = str(row.vessel_rws_class), bool(row.is_loaded)
        profile = caches.profile(vessel, is_loaded)
        data = KeyData(
            key_weights(context, caches, row),
            travel.distance_km / travel.speed_km_per_h
            + travel.reported_fixed_time_h
            + profile.total_h,
            profile.total_h,
        )
        if context.settings.metric is RouteCostMetric.TRAVEL_COST_EUR:
            data.rates = context.cost_inputs.rates_for(vessel, str(row.appearance))
            data.scenario = context.cost_inputs.scenario
            fuels = caches.fuel_rates(vessel, float(row.routing_loading_ratio))
            data.fuel_rate_l_per_h = np.array([f.fuel_rate_l_per_h for f in fuels])
        self._keys[key] = data
        return data

    def _wait_s(self, edge: int, forward: bool, length_m: float, beam_m: float) -> float:
        side = entry_side(forward=forward)
        return sum(
            self.locks.expected_wait_s(p.lock_id, side, length_m, beam_m)
            for p in self.placements.on_edge(edge, forward=forward)
        )

    def _edge_weights(
        self, data: KeyData, edges: npt.NDArray[np.int64], extra_h: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        """Search weight of some edges with ``extra_h`` of reported waiting time added."""
        travel = self.context.travel
        if data.fuel_rate_l_per_h is None:
            return data.weights[edges] + extra_h
        return search_weights(
            self.context.settings.metric,
            EdgeTravel(
                travel.distance_km[edges],
                travel.speed_km_per_h[edges],
                travel.reported_fixed_time_h[edges] + extra_h,
            ),
            data.penalty_h[edges],
            rates=data.rates,
            scenario=data.scenario,
            fuel_rate_l_per_h=data.fuel_rate_l_per_h[edges],
        )

    def _distance(
        self, state: VesselState, node: int, time_h: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        """Free-flow choice time from ``node`` to every node. The network is undirected, so the
        same vector is also the time to ``node``. Cached with a bounded number of entries."""
        key = (state, node)
        found = self._distances.get(key)
        if found is not None:
            self._distances.move_to_end(key)
            return found
        matrix = self._time_matrices.get(state)
        if matrix is None:
            matrix = self.context.network.search_matrix(time_h)
            self._time_matrices[state] = matrix
        distances: npt.NDArray[np.float64] = dijkstra(matrix, directed=True, indices=node)
        self._distances[key] = distances
        if len(self._distances) > DISTANCE_CACHE_SIZE:
            self._distances.popitem(last=False)
        return distances

    def _route_waits(
        self, vessel: RouteVessel, ahead: list[int], position: int
    ) -> dict[int, tuple[int, bool, float]]:
        """Expected wait in seconds at the locks the vessel knows of on its own route.

        Keyed by route position, with the edge, its direction and the wait. With a lookahead,
        locks that the route reaches later than ``lookahead_h`` of sailing are not known.
        """
        network = self.context.network
        nodes, edges = vessel.nodes, vessel.edges
        before = None
        if self.lookahead_h is not None:
            rest = self._sail_h[np.asarray(edges[position:], dtype=np.int64)]
            before = np.cumsum(rest) - rest
        found: dict[int, tuple[int, bool, float]] = {}
        for k in ahead:
            if before is not None and before[k - position] > self.lookahead_h:
                break
            edge = edges[k]
            forward = bool(network.edge_from[edge] == nodes[k])
            found[k] = (edge, forward, self._wait_s(edge, forward, vessel.L, vessel.B))
        return found

    def _scope(
        self,
        vessel: RouteVessel,
        data: KeyData,
        state: VesselState,
        waiting_h: float,
    ) -> npt.NDArray[np.bool_]:
        """Nodes a better route can use. Excludes the nodes already passed.

        A path from the current node to the destination through ``n`` costs at least
        ``d(origin, n) - d(origin, current) + d(n, destination)`` (triangle inequality), and
        only a path within the route's own free-flow time plus the expected wait, minus the
        threshold, can win. Nothing outside this set can matter, so the search is unchanged.
        """
        network = self.context.network
        position = vessel.position_on_route
        nodes = vessel.nodes
        current = nodes[position]
        blocked = np.zeros(len(network.node_ids), dtype=bool)
        blocked[nodes[:position]] = True
        if self.exhaustive:
            return ~blocked
        remaining = np.asarray(vessel.edges[position:], dtype=np.int64)
        budget = (
            float(data.time_h[remaining].sum()) + waiting_h - self.threshold_h
        ) + CORRIDOR_TOLERANCE_H
        from_origin = self._distance(state, nodes[0], data.time_h)
        to_destination = self._distance(state, nodes[-1], data.time_h)
        scope: npt.NDArray[np.bool_] = (
            from_origin + to_destination - from_origin[current] <= budget
        ) & ~blocked
        scope[current] = True
        return scope

    def _costs(
        self,
        vessel: RouteVessel,
        data: KeyData,
        route_waits: dict[int, tuple[int, bool, float]],
        allowed: npt.NDArray[np.bool_],
    ) -> DirectedCosts:
        """Search weights and expected waits per edge and direction for one decision.

        Only waits the vessel knows of are used: the locks on its route (``route_waits``) and
        the locks on ``allowed`` edges that are within the lookahead. Every other lock edge keeps
        its free-flow weight, and the ones outside the corridor are not searched at all.
        """
        wait_forward = np.zeros(len(data.weights))
        wait_backward = np.zeros(len(data.weights))
        known = set()
        for edge, forward, wait_s in route_waits.values():
            (wait_forward if forward else wait_backward)[edge] = wait_s / SECONDS_PER_HOUR
            known.add((edge, forward))
        if len(self.lock_edges):
            in_scope = allowed[self.lock_edges]
            if self.lookahead_h is not None and self._sail_matrix is not None and in_scope.any():
                current = vessel.nodes[vessel.position_on_route]
                sail = dijkstra(
                    self._sail_matrix, directed=True, indices=current, limit=self.lookahead_h
                )
                near = np.minimum(sail[self._lock_from], sail[self._lock_to]) <= self.lookahead_h
                in_scope &= near
            for edge in self.lock_edges[in_scope].tolist():
                for forward, waits in ((True, wait_forward), (False, wait_backward)):
                    if (edge, forward) not in known:
                        wait_s = self._wait_s(edge, forward, vessel.L, vessel.B)
                        waits[edge] = wait_s / SECONDS_PER_HOUR
        weights = []
        for waits in (wait_forward, wait_backward):
            adjusted = data.weights.copy()
            hit = np.flatnonzero(waits > 0)
            if len(hit):
                adjusted[hit] = self._edge_weights(data, hit, waits[hit])
            weights.append(adjusted)
        return DirectedCosts(weights[0], weights[1], wait_forward, wait_backward)

    def consider(self, vessel: RouteVessel) -> bool:
        """Switch ``vessel`` to a clearly better route from its current node. True if it did."""
        network = self.context.network
        position = vessel.position_on_route
        nodes, edges = vessel.nodes, vessel.edges
        current = nodes[position]
        arrived_by_edge = 1 if position > 0 else 0
        if self.degree[current] - arrived_by_edge < 2:
            return False
        ahead = [k for k in vessel.lock_positions if k >= position]
        if not ahead:
            return False
        route_waits = self._route_waits(vessel, ahead, position)
        waiting_h = sum(wait for _, _, wait in route_waits.values()) / SECONDS_PER_HOUR
        if waiting_h < self.threshold_h:
            return False

        data = self.key_data(vessel.trip_row)
        state: VesselState = (
            str(vessel.trip_row.vessel_rws_class),
            bool(vessel.trip_row.is_loaded),
        )
        scope = self._scope(vessel, data, state, waiting_h)
        allowed = scope[network.edge_from] & scope[network.edge_to]
        remaining_edges = np.asarray(edges[position:], dtype=np.int64)
        if not self.exhaustive:
            spare = allowed.copy()
            spare[remaining_edges] = False
            if not spare.any():
                return False
        costs = self._costs(vessel, data, route_waits, allowed)

        def totals(path_nodes: list[int], path_edges: list[int]) -> tuple[float, float]:
            edge_array = np.asarray(path_edges, dtype=np.int64)
            forward = network.edge_from[edge_array] == np.asarray(path_nodes[:-1])
            metric = np.where(
                forward, costs.weight_forward[edge_array], costs.weight_backward[edge_array]
            ).sum()
            wait = np.where(
                forward, costs.wait_forward_h[edge_array], costs.wait_backward_h[edge_array]
            )
            return float(metric), float((data.time_h[edge_array] + wait).sum())

        remaining = nodes[position:]
        current_metric, current_time = totals(remaining, edges[position:])

        directed = np.concatenate([costs.weight_forward, costs.weight_backward])[self._order]
        directed = np.maximum(directed, MIN_SEARCH_WEIGHT)
        reachable = scope[self._arc_rows] & scope[self._indices]
        directed[~reachable] = np.inf
        size = len(scope)
        matrix = csr_matrix((directed, self._indices, self._indptr), shape=(size, size))
        destination = nodes[-1]
        # An alternative has to be cheaper than the current route, so the search stops there.
        distances, predecessors = dijkstra(
            matrix,
            directed=True,
            indices=current,
            return_predecessors=True,
            limit=current_metric,
        )
        if not np.isfinite(distances[destination]):
            return False
        path = [destination]
        while path[-1] != current:
            path.append(int(predecessors[path[-1]]))
        path.reverse()
        if path == remaining:
            return False
        alternative_metric, alternative_time = totals(path, network.path_edges(path))
        saving_h = current_time - alternative_time
        if (
            alternative_metric >= current_metric * (1.0 - RELATIVE_TOLERANCE)
            or saving_h < self.threshold_h
        ):
            return False
        self.events.append(
            RerouteEvent(
                vessel.voyage_index,
                float(vessel.env.now),
                network.node_ids[current],
                waiting_h,
                saving_h,
                len(remaining) - 1,
                len(path) - 1,
            )
        )
        vessel.set_route([*nodes[:position], *path])
        return True
