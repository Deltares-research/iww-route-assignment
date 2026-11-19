"""Simple lock behaviour for the route simulation.

A lock complex has one or more chambers. Each chamber has a queue on side A and on side B and
carries vessels from one side to the other in ``operation_time_s``. There are no gates and no
lock-keeper process: a chamber takes the vessels waiting on the side it is on, moves, and
takes the vessels waiting on the other side. It sails empty to the other side when only
vessels there are waiting.

Which vessels share a chamber is decided in :mod:`.packing`. A vessel that is larger than
every chamber still passes, alone, so a single vessel can never stop the simulation.

Use :meth:`Locks.pass_lock` as an OpenTNSim ``on_pass_edge_functions`` entry::

    locks = Locks(env, complexes, placements, network)
    vessel.on_pass_edge_functions = [functools.partial(locks.pass_lock, vessel=vessel)]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import opentnsim.core as core
import simpy

from ..assignment.lock_placement import SIDE_A, SIDE_B, LockPlacements, entry_side
from ..assignment.network import AssignmentNetwork
from ..domain.locks import ChamberSpec, LockComplex
from .packing import select_vessels

OTHER_SIDE = {SIDE_A: SIDE_B, SIDE_B: SIDE_A}


@dataclass(frozen=True, slots=True)
class LockPassage:
    """One vessel passing one lock. Times are simulation seconds."""

    voyage_index: int
    vessel: str
    lock_id: str
    chamber: str
    side: str
    edge_index: int
    arrival_s: float
    entry_s: float
    exit_s: float
    oversize: bool

    @property
    def wait_s(self) -> float:
        return self.entry_s - self.arrival_s

    @property
    def service_s(self) -> float:
        return self.exit_s - self.entry_s


@dataclass(slots=True, eq=False)
class LockRequest:
    """A vessel waiting for, or inside, a chamber."""

    vessel: Any
    side: str
    arrival_s: float
    entered: simpy.Event
    exited: simpy.Event
    entry_s: float = 0.0
    exit_s: float = 0.0
    oversize: bool = False


class Chamber(core.Log):
    """One chamber with a queue per side.

    ``side`` is the side the chamber is on, or leaves from while it ``moving``. The chamber
    starts idle on ``start_side``.
    """

    def __init__(
        self,
        env: simpy.Environment,
        lock_id: str,
        spec: ChamberSpec,
        *,
        start_side: str = SIDE_A,
        keep_logbook: bool = False,
    ) -> None:
        super().__init__(env=env)
        self.env = env
        self.lock_id = lock_id
        self.spec = spec
        self.name = f"{lock_id}:{spec.chamber_id}"
        self.side = start_side
        self.moving = False
        self.move_end_s = 0.0
        self.keep_logbook = keep_logbook
        self.queues: dict[str, list[LockRequest]] = {SIDE_A: [], SIDE_B: []}
        self.max_queue = {SIDE_A: 0, SIDE_B: 0}
        self.loads = 0
        # Bumped whenever a queue changes, so a queue-based estimate can be reused until then.
        self._queue_version = 0
        self._queue_wait_cache: dict[tuple[str, float, float], tuple[int, float]] = {}
        self._wake = env.event()
        env.process(self._run())

    @property
    def state(self) -> dict[str, Any]:
        return {
            "chamber": self.name,
            "side": self.side,
            "moving": self.moving,
            "queue_a": len(self.queues[SIDE_A]),
            "queue_b": len(self.queues[SIDE_B]),
        }

    def _log(self, message: str) -> None:
        if self.keep_logbook:
            self.log_entry_v0(message, self.env.now, self.state, None)

    def submit(self, vessel: Any, side: str) -> LockRequest:
        """Join the queue on ``side``. The chamber starts as soon as it can take the vessel."""
        request = LockRequest(vessel, side, self.env.now, self.env.event(), self.env.event())
        queue = self.queues[side]
        queue.append(request)
        self._queue_version += 1
        self.max_queue[side] = max(self.max_queue[side], len(queue))
        if not self._wake.triggered:
            self._wake.succeed()
        return request

    def _run(self) -> Any:
        spec = self.spec
        while True:
            queue = self.queues[self.side]
            if not queue and not self.queues[OTHER_SIDE[self.side]]:
                self._wake = self.env.event()
                yield self._wake
                continue
            batch: list[LockRequest] = []
            if queue:
                taken = select_vessels(
                    [(r.vessel.L, r.vessel.B) for r in queue], spec.length_m, spec.width_m
                )
                batch = [queue[i] for i in taken]
                for position in sorted(taken, reverse=True):
                    del queue[position]
                self._queue_version += 1
                for request in batch:
                    request.entry_s = self.env.now
                    request.oversize = not spec.fits(request.vessel.L, request.vessel.B)
                    request.entered.succeed()
                self._log(f"load {len(batch)} vessels")
            self.moving = True
            self.move_end_s = self.env.now + spec.operation_time_s
            yield self.env.timeout(spec.operation_time_s)
            self.moving = False
            self.side = OTHER_SIDE[self.side]
            self.loads += bool(batch)
            for request in batch:
                request.exit_s = self.env.now
                request.exited.succeed()
            self._log("arrived")

    def expected_wait_s(self, side: str, length_m: float, beam_m: float) -> float:
        """Estimated wait for a vessel that joins the queue on ``side`` now.

        The time until the chamber is open on that side, plus a full round trip for every
        chamber load of vessels queued ahead. Vessels already in the chamber are not
        counted: the chamber is moving them. It is an estimate for route choice, not a
        forecast.
        """
        operation = self.spec.operation_time_s
        remaining = max(0.0, self.move_end_s - self.env.now) if self.moving else 0.0
        if self.moving:
            available = remaining if OTHER_SIDE[self.side] == side else remaining + operation
        else:
            available = 0.0 if self.side == side else operation
        ahead = self._queued_wait_s(side, length_m, beam_m)
        return available + ahead

    def _queued_wait_s(self, side: str, length_m: float, beam_m: float) -> float:
        """Wait caused by the vessels queued on ``side``: a round trip per chamber load ahead.

        It depends on the queue and the vessel size only, so it is kept until a queue changes.
        """
        key = (side, length_m, beam_m)
        cached = self._queue_wait_cache.get(key)
        if cached is not None and cached[0] == self._queue_version:
            return cached[1]
        queued = [(r.vessel.L, r.vessel.B) for r in self.queues[side]]
        extra = 0.0
        if queued:
            waiting = [*queued, (length_m, beam_m)]
            last = len(waiting) - 1
            start = loads = 0
            while True:
                taken = len(select_vessels(waiting[start:], self.spec.length_m, self.spec.width_m))
                loads += 1
                if start + taken > last:
                    break
                start += taken
            extra = (loads - 1) * 2.0 * self.spec.operation_time_s
        self._queue_wait_cache[key] = (self._queue_version, extra)
        return extra


class Lock(core.Log):
    """A lock complex: its chambers, and the choice of chamber for a vessel."""

    def __init__(
        self,
        env: simpy.Environment,
        complex_: LockComplex,
        *,
        start_side: str = SIDE_A,
        keep_logbook: bool = False,
    ) -> None:
        super().__init__(env=env)
        self.env = env
        self.lock_id = complex_.lock_id
        self.name = complex_.name or complex_.lock_id
        self.complex = complex_
        self._fitting: dict[tuple[float, float], list[Chamber]] = {}
        self.chambers = [
            Chamber(env, complex_.lock_id, spec, start_side=start_side, keep_logbook=keep_logbook)
            for spec in complex_.chambers
        ]

    def _candidates(self, length_m: float, beam_m: float) -> list[Chamber]:
        """Chambers the vessel fits in. A vessel that fits none uses the largest chamber."""
        key = (length_m, beam_m)
        found = self._fitting.get(key)
        if found is None:
            found = [c for c in self.chambers if c.spec.fits(length_m, beam_m)]
            if not found:
                largest = self.complex.largest_chamber()
                found = [c for c in self.chambers if c.spec is largest]
            self._fitting[key] = found
        return found

    def best_chamber(self, side: str, length_m: float, beam_m: float) -> tuple[Chamber, float]:
        """The chamber with the shortest expected wait on ``side``, and that wait in seconds."""
        scored = [
            (c.expected_wait_s(side, length_m, beam_m), index, c)
            for index, c in enumerate(self._candidates(length_m, beam_m))
        ]
        wait, _, chamber = min(scored, key=lambda item: (item[0], item[1]))
        return chamber, wait

    def expected_wait_s(self, side: str, length_m: float, beam_m: float) -> float:
        return self.best_chamber(side, length_m, beam_m)[1]


class Locks:
    """All locks of a simulation. Locks are created when first used.

    ``passages`` collects every lock passage. ``network`` and ``placements`` tell which locks
    sit on an edge and from which side a vessel enters; no node naming is needed.
    """

    def __init__(
        self,
        env: simpy.Environment,
        complexes: Mapping[str, LockComplex],
        placements: LockPlacements,
        network: AssignmentNetwork,
        *,
        start_side: str = SIDE_A,
        keep_logbook: bool = False,
    ) -> None:
        self.env = env
        self.complexes = complexes
        self.placements = placements
        self.network = network
        self.start_side = start_side
        self.keep_logbook = keep_logbook
        self.locks: dict[str, Lock] = {}
        self.passages: list[LockPassage] = []
        self._idle_wait: dict[tuple[str, str, float, float], float] = {}

    def lock(self, lock_id: str) -> Lock:
        found = self.locks.get(lock_id)
        if found is None:
            found = Lock(
                self.env,
                self.complexes[lock_id],
                start_side=self.start_side,
                keep_logbook=self.keep_logbook,
            )
            self.locks[lock_id] = found
        return found

    def edge_and_direction(self, origin: str, destination: str) -> tuple[int, bool] | None:
        """Edge index and whether the vessel sails from ``from_node`` to ``to_node``."""
        position = self.network.node_position
        u, v = position.get(str(origin)), position.get(str(destination))
        if u is None or v is None:
            return None
        edge = self.network.edge_by_pair.get((min(u, v), max(u, v)))
        if edge is None:
            return None
        return edge, int(self.network.edge_from[edge]) == u

    def expected_wait_s(self, lock_id: str, side: str, length_m: float, beam_m: float) -> float:
        """Estimated wait at a lock for a vessel arriving now on ``side``."""
        found = self.locks.get(lock_id)
        if found is not None:
            return found.expected_wait_s(side, length_m, beam_m)
        # A lock nobody has used yet has idle chambers on the start side.
        key = (lock_id, side, length_m, beam_m)
        idle = self._idle_wait.get(key)
        if idle is None:
            complex_ = self.complexes[lock_id]
            chambers = complex_.chambers_fitting(length_m, beam_m) or (complex_.largest_chamber(),)
            idle = min(
                0.0 if self.start_side == side else chamber.operation_time_s for chamber in chambers
            )
            self._idle_wait[key] = idle
        return idle

    def pass_lock(self, origin: str, destination: str, vessel: Any) -> Any:
        """Pass every lock on the edge ``origin`` to ``destination``.

        Meant for ``on_pass_edge_functions``. The vessel sails to each lock first, using
        ``vessel.v`` and ``vessel.distance_left_on_edge``. Afterwards
        ``distance_left_on_edge`` holds the distance after the last lock, which the vessel
        then sails as usual. An edge without lock takes no time.
        """
        resolved = self.edge_and_direction(origin, destination)
        if resolved is None:
            return
        edge, forward = resolved
        placed = self.placements.on_edge(edge, forward=forward)
        if not placed:
            return
        side = entry_side(forward=forward)
        total = float(vessel.distance_left_on_edge)
        covered = 0.0
        for placement in placed:
            fraction = placement.fraction if forward else 1.0 - placement.fraction
            if fraction > covered:
                metres = (fraction - covered) * total
                yield self.env.timeout(metres / vessel.v)
                vessel.distance += metres
                covered = fraction
            yield from self._pass(self.lock(placement.lock_id), vessel, side, edge)
        vessel.distance_left_on_edge = (1.0 - covered) * total

    def _pass(self, lock: Lock, vessel: Any, side: str, edge: int) -> Any:
        chamber, _ = lock.best_chamber(side, vessel.L, vessel.B)
        request = chamber.submit(vessel, side)
        yield request.entered
        yield request.exited
        passage = LockPassage(
            int(getattr(vessel, "voyage_index", -1)),
            str(vessel.name),
            lock.lock_id,
            chamber.name,
            side,
            edge,
            request.arrival_s,
            request.entry_s,
            request.exit_s,
            request.oversize,
        )
        self.passages.append(passage)
        recorded = getattr(vessel, "lock_passages", None)
        if recorded is not None:
            recorded.append(passage)
