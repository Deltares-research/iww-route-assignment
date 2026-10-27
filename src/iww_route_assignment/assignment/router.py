"""Shortest-path search per routing key and origin, then exact repricing per voyage.

Voyages that share a routing key share edge weights. For the time metric the key is the
vessel class and loading state. For the cost metric it also holds the appearance and the
rounded loading ratio. One search from each origin node serves every voyage from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.sparse.csgraph import dijkstra

from ..domain.enums import Confidence, PenaltyType, RouteCostMetric, VoyageStatus
from ..domain.models import ResolvedFuelRate, TripCostInput
from ..exceptions import IWWRouteAssignmentError
from ..navigability import RestrictionIndex
from .network import AssignmentNetwork
from .penalties import PenaltyProfile, penalty_profile
from .pricing import CostInputs, EdgeTravel, FuelRates, RoutePrice, price_route, search_weights
from .settings import AssignmentSettings, NavigabilityPenalties, RoutePreferences
from .voyages import VesselCatalogue

CONFIDENCE_ORDER = (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW, Confidence.FALLBACK)
PENALTY_COLUMNS = tuple(f"penalty_{kind.value}_h" for kind in PenaltyType)
EDGE_COLUMNS = (
    "voyage_index",
    "seq",
    "edge_index",
    "from_node",
    "to_node",
    *PENALTY_COLUMNS,
    "penalty_total_h",
    "reported_time_h",
    "reported_cost_eur",
    "choice_cost_eur",
    "fuel_l",
    "fuel_rate_l_per_h",
    "fuel_method",
    "fuel_confidence",
)


@dataclass(frozen=True, slots=True, eq=False)
class RoutingContext:
    """Everything a routing task reads. Built once, shared by every task."""

    network: AssignmentNetwork
    travel: EdgeTravel
    restrictions: RestrictionIndex
    catalogue: VesselCatalogue
    cost_inputs: CostInputs
    settings: AssignmentSettings
    penalties: NavigabilityPenalties
    preferences: RoutePreferences


@dataclass(slots=True)
class RoutingOutput:
    """Rows produced by routing a set of voyages."""

    voyages: list[dict[str, Any]] = field(default_factory=list)
    edges: dict[str, list[npt.NDArray[Any]]] = field(
        default_factory=lambda: {name: [] for name in EDGE_COLUMNS}
    )
    penalised: dict[tuple[Any, ...], list[Any]] = field(default_factory=dict)

    def extend(self, other: RoutingOutput) -> None:
        self.voyages.extend(other.voyages)
        for name in EDGE_COLUMNS:
            self.edges[name].extend(other.edges[name])
        for key, (trips, cargo, classes) in other.penalised.items():
            self._add_penalised(key, trips, cargo, classes)

    def _add_penalised(
        self, key: tuple[Any, ...], trips: int, cargo: float, classes: set[str]
    ) -> None:
        entry = self.penalised.setdefault(key, [0, 0.0, set()])
        entry[0] += trips
        entry[1] += cargo
        entry[2].update(classes)


class _Caches:
    """Per-process caches: penalty profiles per vessel state, fuel rates per ratio."""

    def __init__(self, context: RoutingContext) -> None:
        self.context = context
        self.fuel = FuelRates(context.cost_inputs.fuel_provider)
        self._profiles: dict[tuple[str, bool], PenaltyProfile] = {}
        self._cemts: dict[str, tuple[str, ...]] = {}

    def profile(self, vessel: str, is_loaded: bool) -> PenaltyProfile:
        key = (vessel, is_loaded)
        if key not in self._profiles:
            c = self.context
            self._profiles[key] = penalty_profile(
                c.network,
                c.restrictions,
                c.catalogue.navigability_vessel(vessel, is_loaded),
                c.catalogue.by_class[vessel].vessel_cemt_class,
                c.penalties,
                c.preferences,
            )
        return self._profiles[key]

    def fuel_cemts(self, vessel: str) -> tuple[str, ...]:
        """Waterway class per edge for fuel; the vessel's own class where the edge has none."""
        if vessel not in self._cemts:
            own = self.context.catalogue.by_class[vessel].vessel_cemt_class
            self._cemts[vessel] = tuple(
                edge.waterway_cemt_class or own for edge in self.context.network.edges
            )
        return self._cemts[vessel]

    def fuel_rates(
        self, vessel: str, ratio: float, edges: npt.NDArray[np.int64] | None = None
    ) -> tuple[ResolvedFuelRate, ...]:
        cemts = self.fuel_cemts(vessel)
        chosen = cemts if edges is None else [cemts[i] for i in edges]
        return self.fuel.for_edges(vessel, chosen, ratio)


def _worst_confidence(fuels: tuple[ResolvedFuelRate, ...]) -> str:
    """Lowest fuel confidence on the route; empty for a route without edges."""
    worst = max((fuel.confidence for fuel in fuels), key=CONFIDENCE_ORDER.index, default=None)
    return worst.value if worst is not None else ""


def _paths(predecessors: npt.NDArray[np.int32], origin: int, destination: int) -> list[int]:
    nodes = [destination]
    while nodes[-1] != origin:
        nodes.append(int(predecessors[nodes[-1]]))
    nodes.reverse()
    return nodes


def _searches(
    context: RoutingContext, weights: npt.NDArray[np.float64], origins: list[int]
) -> Iterator[tuple[int, npt.NDArray[np.float64], npt.NDArray[np.int32]]]:
    """Run Dijkstra for blocks of origins; yield distances and predecessors per origin."""
    matrix = context.network.search_matrix(weights)
    block = context.settings.origins_per_search
    for start in range(0, len(origins), block):
        chunk = origins[start : start + block]
        distances, predecessors = dijkstra(
            matrix, directed=True, indices=chunk, return_predecessors=True
        )
        for row, origin in enumerate(chunk):
            yield origin, distances[row], predecessors[row]


def _record_edges(
    output: RoutingOutput,
    voyage_index: int,
    edges: npt.NDArray[np.int64],
    nodes: list[int],
    profile: PenaltyProfile,
    price: RoutePrice,
) -> None:
    count = len(edges)
    columns = output.edges
    columns["voyage_index"].append(np.full(count, voyage_index, dtype=np.int64))
    columns["seq"].append(np.arange(count, dtype=np.int64))
    columns["edge_index"].append(edges)
    columns["from_node"].append(np.asarray(nodes[:-1], dtype=np.int64))
    columns["to_node"].append(np.asarray(nodes[1:], dtype=np.int64))
    for kind, name in zip(PenaltyType, PENALTY_COLUMNS, strict=True):
        columns[name].append(profile.hours[kind][edges])
    columns["penalty_total_h"].append(profile.total_h[edges])
    columns["reported_time_h"].append(price.arcs.reported_time_h)
    columns["reported_cost_eur"].append(price.arcs.reported_variable_cost_eur)
    columns["choice_cost_eur"].append(price.arcs.choice_cost_eur)
    columns["fuel_l"].append(price.arcs.sailing_fuel_l + price.arcs.idle_fuel_l)
    columns["fuel_rate_l_per_h"].append(np.array([f.fuel_rate_l_per_h for f in price.fuels]))
    columns["fuel_method"].append(np.array([f.method.value for f in price.fuels], dtype=object))
    columns["fuel_confidence"].append(
        np.array([f.confidence.value for f in price.fuels], dtype=object)
    )


def _record_penalties(
    output: RoutingOutput, edges: npt.NDArray[np.int64], profile: PenaltyProfile, row: Any
) -> int:
    penalised = 0
    for edge in dict.fromkeys(edges.tolist()):
        details = profile.details.get(edge)
        if not details:
            continue
        penalised += 1
        for d in details:
            key = (
                edge,
                d.penalty_type.value,
                d.structure_id or "",
                d.structure_type.value if d.structure_type else "",
                d.limit_m,
                d.penalty_h,
                d.source,
            )
            output._add_penalised(key, 1, float(row.cargo_t), {row.vessel_rws_class})
    return penalised


def route_group(context: RoutingContext, rows: pd.DataFrame, caches: _Caches) -> RoutingOutput:
    """Route every voyage that shares one routing key."""
    output = RoutingOutput()
    metric = context.settings.metric
    first = rows.iloc[0]
    vessel, is_loaded = str(first.vessel_rws_class), bool(first.is_loaded)
    profile = caches.profile(vessel, is_loaded)
    base = {"route_metric": metric.value}

    try:
        if metric is RouteCostMetric.TRAVEL_COST_EUR:
            rates = context.cost_inputs.rates_for(vessel, str(first.appearance))
            fuels = caches.fuel_rates(vessel, float(first.routing_loading_ratio))
            weights = search_weights(
                metric,
                context.travel,
                profile.total_h,
                rates=rates,
                scenario=context.cost_inputs.scenario,
                fuel_rate_l_per_h=np.array([f.fuel_rate_l_per_h for f in fuels]),
            )
        else:
            weights = search_weights(metric, context.travel, profile.total_h)
    except IWWRouteAssignmentError as error:
        for voyage_index in rows["voyage_index"].tolist():
            output.voyages.append(
                {
                    "voyage_index": voyage_index,
                    "status": VoyageStatus.COST_FAIL.value,
                    "detail": f"route search weights: {error}",
                }
                | base
            )
        return output

    by_origin = {int(cast(Any, o)): g for o, g in rows.groupby("origin_node", sort=True)}
    for origin, distances, predecessors in _searches(context, weights, sorted(by_origin)):
        for row in cast(Iterable[Any], by_origin[origin].itertuples(index=False)):
            record = {"voyage_index": row.voyage_index} | base
            destination = int(row.destination_node)
            if not np.isfinite(distances[destination]):
                output.voyages.append(
                    record | {"status": VoyageStatus.NO_PATH.value, "detail": "no path in network"}
                )
                continue
            nodes = _paths(predecessors, int(origin), destination)
            edges = np.asarray(context.network.path_edges(nodes), dtype=np.int64)
            try:
                price = price_route(
                    edges,
                    context.travel,
                    profile.total_h,
                    caches.fuel_rates(vessel, float(row.loading_ratio), edges),
                    context.cost_inputs.rates_for(vessel, str(row.appearance)),
                    context.cost_inputs.scenario,
                    TripCostInput(cargo_t=float(row.cargo_t), is_loaded=bool(row.is_loaded)),
                )
            except IWWRouteAssignmentError as error:
                output.voyages.append(
                    record | {"status": VoyageStatus.COST_FAIL.value, "detail": str(error)}
                )
                continue
            _record_edges(output, int(row.voyage_index), edges, nodes, profile, price)
            output.voyages.append(
                record
                | {
                    "status": VoyageStatus.SUCCESS.value,
                    "detail": "",
                    "n_edges": len(edges),
                    "distance_km": float(context.travel.distance_km[edges].sum()),
                    "route_weight": float(distances[destination]),
                    "reported_time_h": float(price.arcs.reported_time_h.sum()),
                    "choice_time_h": float(price.arcs.choice_time_h.sum()),
                    "fuel_l": float((price.arcs.sailing_fuel_l + price.arcs.idle_fuel_l).sum()),
                    "fixed_cost_eur": price.trip.fixed_cost_eur,
                    "reported_total_cost_eur": price.trip.reported_total_cost_eur,
                    "choice_total_cost_eur": price.trip.choice_total_cost_eur,
                    "total_cost_eur": price.trip.choice_total_cost_eur,
                    "penalty_total_h": float(profile.total_h[edges].sum()),
                    "penalised_section_count": _record_penalties(output, edges, profile, row),
                    "fuel_lowest_confidence": _worst_confidence(price.fuels),
                    "cost_rate_source": context.cost_inputs.rates_for(
                        vessel, str(row.appearance)
                    ).source,
                }
            )
    return output


_WORKER_CONTEXT: RoutingContext | None = None
_WORKER_CACHES: _Caches | None = None


def _init_worker(context: RoutingContext) -> None:
    global _WORKER_CONTEXT, _WORKER_CACHES
    _WORKER_CONTEXT, _WORKER_CACHES = context, _Caches(context)


def _route_task(groups: list[pd.DataFrame]) -> RoutingOutput:
    if _WORKER_CONTEXT is None or _WORKER_CACHES is None:
        raise RuntimeError("Routing worker was not initialised")
    output = RoutingOutput()
    for rows in groups:
        output.extend(route_group(_WORKER_CONTEXT, rows, _WORKER_CACHES))
    return output


def route_voyages(context: RoutingContext, pending: pd.DataFrame) -> RoutingOutput:
    """Route pending voyages, in worker processes when ``n_workers > 1``.

    Groups are processed in sorted routing-key order, so results do not depend on input
    order or on the number of workers.
    """
    groups = [rows for _, rows in pending.groupby("routing_key", sort=True)]
    output = RoutingOutput()
    workers = context.settings.n_workers
    if workers == 1 or len(groups) < 2:
        caches = _Caches(context)
        for rows in groups:
            output.extend(route_group(context, rows, caches))
        return output

    from concurrent.futures import ProcessPoolExecutor

    # Each worker keeps its own caches, so a vessel state is profiled once per worker.
    tasks = [[rows] for rows in groups]
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(context,)
    ) as executor:
        for result in executor.map(_route_task, tasks):
            output.extend(result)
    return output
