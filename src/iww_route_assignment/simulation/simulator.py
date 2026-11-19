"""Replay assigned trips in time with lock queues, and let vessels avoid long queues.

:class:`LockSimulator` plugs into :func:`~iww_route_assignment.assignment.assign_traffic`
through its ``simulator`` argument. The assignment first finds free-flow routes. The simulator
then sails every trip that passes a lock, from its departure time, with OpenTNSim vessels
over the same network and simple lock chambers (see :mod:`.locks`). Trips without a lock on
their route, without a departure time, or outside the time window keep their free-flow route.

At a node where a vessel has another way to go, it looks at the expected wait at the locks
ahead. When the wait is long enough, and an alternative that does not turn back is clearly
faster, it takes that route (see :mod:`.rerouting`).

The routes that were sailed are priced again with the exact waiting and chamber time of each
lock passage. Time in the chamber replaces the fixed passage time of that lock.
"""

from __future__ import annotations

import functools
import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd
import simpy

from ..assignment.lock_placement import SIDE_A, SIDE_B, LockPlacements, place_locks
from ..assignment.network import AssignmentNetwork
from ..assignment.router import (
    EDGE_COLUMNS,
    RoutingCaches,
    RoutingContext,
    RoutingOutput,
    key_weights,
    record_penalties,
    record_route,
)
from ..assignment.simulation_port import SimulationOutcome
from ..domain.enums import TripStatus
from ..domain.locks import DEFAULT_OPERATION_TIME_MIN, LockComplex
from ..exceptions import ValidationError
from ..io.locks import load_lock_data
from .graph import build_opentnsim_graph
from .locks import LockPassage, Locks
from .rerouting import Rerouter
from .vessel import RouteData, RouteVessel

logger = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600.0
SIMULATED_EDGE_COLUMNS = ("lock_waiting_h", "lock_service_h")

PASSAGE_COLUMNS = (
    "voyage_index",
    "trip_id",
    "lock_id",
    "lock_name",
    "chamber",
    "side",
    "edge_index",
    "arrival_time",
    "entry_time",
    "exit_time",
    "wait_min",
    "service_min",
    "oversize",
)
REROUTE_COLUMNS = (
    "voyage_index",
    "trip_id",
    "time",
    "node_id",
    "expected_wait_h",
    "saving_h",
    "old_edges",
    "new_edges",
)
LOCK_SUMMARY_COLUMNS = (
    "lock_id",
    "lock_name",
    "passages",
    "oversize_passages",
    "mean_wait_min",
    "max_wait_min",
    "total_wait_h",
    "max_queue_a",
    "max_queue_b",
)


@dataclass(frozen=True, slots=True)
class LockSimulationSettings:
    """How trips are replayed.

    ``reroute_threshold_h`` is the minimum waiting time ahead that triggers a reroute check,
    and the minimum saving in choice time (travel time plus penalty hours) an alternative must
    give. ``lookahead_h`` limits how far a vessel knows the lock queues, in hours of sailing:
    8 hours by default, ``None`` for every queue on the network. ``window_start`` (inclusive)
    and ``window_end`` (exclusive) limit the trips that are simulated, by departure time.
    ``start_side`` is the side every chamber starts on. ``exhaustive_search`` makes a reroute
    search the whole network instead of the corridor that can matter. It gives the same
    decisions and is only there to check that, so it is slow.
    """

    reroute: bool = True
    reroute_threshold_h: float = 0.5
    window_start: datetime | None = None
    window_end: datetime | None = None
    start_side: str = SIDE_A
    keep_logbook: bool = False
    lookahead_h: float | None = 8.0
    exhaustive_search: bool = False

    def __post_init__(self) -> None:
        if not np.isfinite(self.reroute_threshold_h) or self.reroute_threshold_h < 0:
            raise ValidationError("reroute_threshold_h must be finite and non-negative")
        if self.lookahead_h is not None and (
            not np.isfinite(self.lookahead_h) or self.lookahead_h <= 0
        ):
            raise ValidationError("lookahead_h must be finite and positive, or None")
        if self.start_side not in {SIDE_A, SIDE_B}:
            raise ValidationError("start_side must be 'A' or 'B'")
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end <= self.window_start
        ):
            raise ValidationError("window_end must be after window_start")


@dataclass(slots=True)
class _Sailed:
    """What a vessel did: the route it sailed and its lock passages."""

    nodes: list[int]
    passages: list[LockPassage]
    finished_s: float


@dataclass(slots=True)
class _Selection:
    """Which trips are simulated, and why the others are not."""

    trips: list[int] = field(default_factory=list)
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "trips_without_lock": 0,
            "trips_without_departure_time": 0,
            "trips_outside_window": 0,
        }
    )


def _static_routes(static: RoutingOutput) -> dict[int, list[int]]:
    """Node path per trip from the free-flow routes."""
    routes: dict[int, list[int]] = {}
    for trip, origin, destination in zip(
        static.edges["voyage_index"],
        static.edges["from_node"],
        static.edges["to_node"],
        strict=True,
    ):
        if len(trip):
            routes[int(trip[0])] = [int(origin[0]), *(int(n) for n in destination)]
    return routes


@dataclass(slots=True, eq=False)
class _Run:
    """State of one simulation run."""

    context: RoutingContext
    caches: RoutingCaches
    rows: Mapping[int, Any]
    selection: _Selection
    sailed: dict[int, _Sailed] = field(default_factory=dict)
    planned: Mapping[int, list[int]] = field(default_factory=dict)
    locks: Locks | None = None
    rerouter: Rerouter | None = None
    start: pd.Timestamp | None = None


class LockSimulator:
    """A :class:`~iww_route_assignment.assignment.RouteSimulator` with lock queues."""

    def __init__(
        self,
        complexes: Mapping[str, LockComplex],
        placements: LockPlacements,
        settings: LockSimulationSettings | None = None,
    ) -> None:
        self.complexes = complexes
        self.placements = placements
        self.settings = settings or LockSimulationSettings()
        self.data_report: Mapping[str, Any] = {}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        network: AssignmentNetwork,
        settings: LockSimulationSettings | None = None,
        *,
        default_operation_time_min: float = DEFAULT_OPERATION_TIME_MIN,
        max_distance_m: float | None = None,
    ) -> LockSimulator:
        """Build a simulator from the EuRIS ``dropins-euris-detailed`` folder.

        Reads the lock and chamber files and the section links, and places every lock on an
        edge of ``network``. The reports of both steps are kept in the run summary.
        """
        data = load_lock_data(directory, default_operation_time_min=default_operation_time_min)
        placements = place_locks(
            network, data.complexes, data.section_locks, max_distance_m=max_distance_m
        )
        simulator = cls(data.complexes, placements, settings)
        simulator.data_report = data.report
        return simulator

    def simulate(
        self, context: RoutingContext, trips: pd.DataFrame, static: RoutingOutput
    ) -> SimulationOutcome:
        """Sail the trips that pass a lock and return all routes, repriced."""
        network = context.network
        settings = self.settings
        routes = _static_routes(static)
        records = cast(Iterable[Any], trips.itertuples(index=False))
        rows: dict[int, Any] = {int(r.voyage_index): r for r in records}
        has_lock = np.zeros(network.edge_count, dtype=bool)
        has_lock[list(self.placements.by_edge)] = True
        selection = self._select(static, routes, rows, network, has_lock)
        run = _Run(context, RoutingCaches(context), rows, selection, planned=routes)

        if selection.trips:
            start = pd.Timestamp(min(rows[i].departure_time for i in selection.trips))
            run.start = start
            env = simpy.Environment()
            env.graph = env.FG = build_opentnsim_graph(network)
            run.locks = Locks(
                env,
                self.complexes,
                self.placements,
                network,
                start_side=settings.start_side,
                keep_logbook=settings.keep_logbook,
            )
            if settings.reroute:
                run.rerouter = Rerouter(
                    context,
                    run.caches,
                    run.locks,
                    self.placements,
                    settings.reroute_threshold_h,
                    settings.lookahead_h,
                    exhaustive=settings.exhaustive_search,
                )
            data = RouteData.from_network(network, np.array(sorted(self.placements.by_edge)))
            for index in selection.trips:
                delay = float((rows[index].departure_time - start).total_seconds())
                env.process(self._sail(env, data, run, routes[index], rows[index], delay))
            env.run()
            logger.info(
                "Simulated %d trips: %d lock passages, %d reroutes",
                len(selection.trips),
                len(run.locks.passages),
                len(run.rerouter.events) if run.rerouter else 0,
            )
        output = self._build_output(run, static)
        return SimulationOutcome(output, self._tables(run), self._summary(run))

    def _select(
        self,
        static: RoutingOutput,
        routes: Mapping[int, list[int]],
        rows: Mapping[int, Any],
        network: AssignmentNetwork,
        has_lock: npt.NDArray[np.bool_],
    ) -> _Selection:
        selection = _Selection()
        settings = self.settings
        for record in static.trips:
            if record["status"] != TripStatus.SUCCESS.value:
                continue
            index = int(record["voyage_index"])
            nodes = routes.get(index)
            if nodes is None or not has_lock[network.path_edges(nodes)].any():
                selection.counts["trips_without_lock"] += 1
                continue
            departure = rows[index].departure_time
            if pd.isna(departure):
                selection.counts["trips_without_departure_time"] += 1
            elif (settings.window_start is not None and departure < settings.window_start) or (
                settings.window_end is not None and departure >= settings.window_end
            ):
                selection.counts["trips_outside_window"] += 1
            else:
                selection.trips.append(index)
        return selection

    @staticmethod
    def _sail(
        env: simpy.Environment,
        data: RouteData,
        run: _Run,
        nodes: list[int],
        row: Any,
        delay_s: float,
    ) -> Any:
        assert run.locks is not None
        yield env.timeout(delay_s)
        reference = run.context.catalogue.by_class[str(row.vessel_rws_class)]
        vessel = RouteVessel(
            env=env,
            data=data,
            voyage_index=int(row.voyage_index),
            name=str(row.trip_id),
            length_m=reference.length_m,
            beam_m=reference.beam_m,
            nodes=nodes,
            trip_row=row,
            rerouter=run.rerouter,
        )
        vessel.on_pass_edge_functions = [functools.partial(run.locks.pass_lock, vessel=vessel)]
        yield from vessel.move()
        run.sailed[vessel.voyage_index] = _Sailed(
            vessel.nodes, vessel.lock_passages, float(vessel.finished_s or env.now)
        )

    def _static_passage_h(
        self, network: AssignmentNetwork, edges: list[int]
    ) -> npt.NDArray[np.float64]:
        """Fixed passage time of the simulated locks on each edge. The simulation replaces it."""
        result = np.zeros(len(edges))
        for k, edge in enumerate(edges):
            placed = len(self.placements.by_edge.get(edge, ()))
            info = network.edges[edge]
            if placed and info.lock_count:
                result[k] = (
                    min(placed, info.lock_count) * info.lock_service_time_h / info.lock_count
                )
        return result

    def _build_output(self, run: _Run, static: RoutingOutput) -> RoutingOutput:
        context, caches, rows, sailed = run.context, run.caches, run.rows, run.sailed
        network = context.network
        output = RoutingOutput()
        for name in SIMULATED_EDGE_COLUMNS:
            output.edges[name] = []

        # Trips that were not simulated keep their free-flow route and numbers.
        for record in static.trips:
            if int(record["voyage_index"]) in sailed:
                continue
            kept = dict(record)
            if record["status"] == TripStatus.SUCCESS.value:
                kept |= {
                    "simulated": False,
                    "n_reroutes": 0,
                    "lock_waiting_h": 0.0,
                    "lock_service_h": 0.0,
                }
            output.trips.append(kept)
        for position, trip in enumerate(static.edges["voyage_index"]):
            if len(trip) == 0 or int(trip[0]) in sailed:
                continue
            for name in EDGE_COLUMNS:
                output.edges[name].append(static.edges[name][position])
            for name in SIMULATED_EDGE_COLUMNS:
                output.edges[name].append(np.zeros(len(trip)))
            row = rows[int(trip[0])]
            profile = caches.profile(str(row.vessel_rws_class), bool(row.is_loaded))
            record_penalties(output, static.edges["edge_index"][position], profile, row)

        reroutes: Mapping[int, int] = (
            Counter(e.voyage_index for e in run.rerouter.events) if run.rerouter else {}
        )
        weights: dict[str, npt.NDArray[np.float64]] = {}
        metric = context.settings.metric.value
        for index, result in sorted(sailed.items()):
            row = rows[index]
            key = str(row.routing_key)
            if key not in weights:
                weights[key] = key_weights(context, caches, row)
            edges = network.path_edges(result.nodes)
            wait_h = np.zeros(len(edges))
            service_h = np.zeros(len(edges))
            edge_position = {edge: k for k, edge in enumerate(edges)}
            for passage in result.passages:
                k = edge_position[passage.edge_index]
                wait_h[k] += passage.wait_s / SECONDS_PER_HOUR
                service_h[k] += passage.service_s / SECONDS_PER_HOUR
            record_route(
                context,
                caches,
                output,
                row,
                result.nodes,
                float(weights[key][edges].sum()),
                {"voyage_index": index, "route_metric": metric},
                caches.profile(str(row.vessel_rws_class), bool(row.is_loaded)),
                fixed_time_adjustment_h=wait_h + service_h - self._static_passage_h(network, edges),
                extra_edge_columns={"lock_waiting_h": wait_h, "lock_service_h": service_h},
                extra_trip_fields={
                    "simulated": True,
                    "n_reroutes": reroutes.get(index, 0),
                    "lock_waiting_h": float(wait_h.sum()),
                    "lock_service_h": float(service_h.sum()),
                },
            )
        return output

    def _time(self, run: _Run, seconds: float) -> pd.Timestamp:
        assert run.start is not None
        return pd.Timestamp(run.start) + pd.to_timedelta(seconds, unit="s")

    def _tables(self, run: _Run) -> dict[str, pd.DataFrame]:
        trip_ids = {i: str(r.trip_id) for i, r in run.rows.items()}
        passages = run.locks.passages if run.locks else []
        passage_rows = [
            {
                "voyage_index": p.voyage_index,
                "trip_id": trip_ids[p.voyage_index],
                "lock_id": p.lock_id,
                "lock_name": self.complexes[p.lock_id].name,
                "chamber": p.chamber,
                "side": p.side,
                "edge_index": p.edge_index,
                "arrival_time": self._time(run, p.arrival_s),
                "entry_time": self._time(run, p.entry_s),
                "exit_time": self._time(run, p.exit_s),
                "wait_min": p.wait_s / 60.0,
                "service_min": p.service_s / 60.0,
                "oversize": p.oversize,
            }
            for p in passages
        ]
        events = run.rerouter.events if run.rerouter else []
        reroute_rows = [
            {
                "voyage_index": e.voyage_index,
                "trip_id": trip_ids[e.voyage_index],
                "time": self._time(run, e.time_s),
                "node_id": e.node,
                "expected_wait_h": e.expected_wait_h,
                "saving_h": e.saving_h,
                "old_edges": e.old_edges,
                "new_edges": e.new_edges,
            }
            for e in events
        ]
        passage_table = pd.DataFrame(passage_rows, columns=list(PASSAGE_COLUMNS))
        return {
            "planned_edges": self._planned_edges(run),
            "lock_passages": passage_table,
            "lock_summary": self._lock_summary(run, passage_table),
            "reroutes": pd.DataFrame(reroute_rows, columns=list(REROUTE_COLUMNS)),
        }

    def _planned_edges(self, run: _Run) -> pd.DataFrame:
        """Free-flow route of every simulated trip, to compare with the route sailed."""
        network = run.context.network
        rows = []
        for index in sorted(run.sailed):
            nodes = run.planned[index]
            row = run.rows[index]
            penalty_h = run.caches.profile(str(row.vessel_rws_class), bool(row.is_loaded)).total_h
            sailed_edges = set(network.path_edges(run.sailed[index].nodes))
            for seq, edge in enumerate(network.path_edges(nodes)):
                rows.append(
                    {
                        "voyage_index": index,
                        "trip_id": str(run.rows[index].trip_id),
                        "seq": seq,
                        "edge_index": edge,
                        "arc_id": network.edges[edge].arc_id,
                        "from_node": network.node_ids[nodes[seq]],
                        "to_node": network.node_ids[nodes[seq + 1]],
                        "penalty_h": float(penalty_h[edge]),
                        "sailed": edge in sailed_edges,
                    }
                )
        columns = [
            "voyage_index",
            "trip_id",
            "seq",
            "edge_index",
            "arc_id",
            "from_node",
            "to_node",
            "penalty_h",
            "sailed",
        ]
        return pd.DataFrame(rows, columns=columns)

    def _lock_summary(self, run: _Run, passages: pd.DataFrame) -> pd.DataFrame:
        if passages.empty or run.locks is None:
            return pd.DataFrame(columns=list(LOCK_SUMMARY_COLUMNS))
        grouped = passages.groupby("lock_id", sort=True)
        summary = grouped.agg(
            lock_name=("lock_name", "first"),
            passages=("voyage_index", "size"),
            oversize_passages=("oversize", "sum"),
            mean_wait_min=("wait_min", "mean"),
            max_wait_min=("wait_min", "max"),
            total_wait_h=("wait_min", lambda w: float(w.sum()) / 60.0),
        ).reset_index()
        queue = {
            lock_id: tuple(
                max((c.max_queue[side] for c in lock.chambers), default=0)
                for side in (SIDE_A, SIDE_B)
            )
            for lock_id, lock in run.locks.locks.items()
        }
        summary["max_queue_a"] = [queue[i][0] for i in summary["lock_id"]]
        summary["max_queue_b"] = [queue[i][1] for i in summary["lock_id"]]
        return summary[list(LOCK_SUMMARY_COLUMNS)]

    def _summary(self, run: _Run) -> dict[str, Any]:
        waits = [p.wait_s for p in run.locks.passages] if run.locks else []
        return {
            "trips_simulated": len(run.sailed),
            **run.selection.counts,
            "lock_passages": len(waits),
            "oversize_passages": sum(p.oversize for p in run.locks.passages) if run.locks else 0,
            "reroutes": len(run.rerouter.events) if run.rerouter else 0,
            "mean_wait_min": float(np.mean(waits)) / 60.0 if waits else 0.0,
            "max_wait_min": float(np.max(waits)) / 60.0 if waits else 0.0,
            "simulation_start": None if run.start is None else str(run.start),
            "simulation_end": (
                str(self._time(run, max(s.finished_s for s in run.sailed.values())))
                if run.sailed
                else None
            ),
            "settings": {
                "reroute": self.settings.reroute,
                "reroute_threshold_h": self.settings.reroute_threshold_h,
                "lookahead_h": self.settings.lookahead_h,
                "exhaustive_search": self.settings.exhaustive_search,
                "window_start": str(self.settings.window_start),
                "window_end": str(self.settings.window_end),
                "start_side": self.settings.start_side,
            },
            "lock_data": dict(self.data_report),
            "placement": dict(self.placements.report),
        }
