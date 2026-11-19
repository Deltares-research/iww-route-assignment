"""Packing and chamber behaviour of the lock simulation, without a network."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("packer2d")
pytest.importorskip("opentnsim")
import simpy  # noqa: E402

from iww_route_assignment.domain import ChamberSpec, LockComplex  # noqa: E402
from iww_route_assignment.simulation import Lock, select_vessels  # noqa: E402


@dataclass
class Boat:
    name: str
    L: float
    B: float


def test_vessels_that_fit_together_enter_together() -> None:
    assert select_vessels([(80, 6), (50, 7), (50, 5), (70, 3)], 110, 14) == [0, 1, 2]
    assert select_vessels([(80, 6), (50, 7), (30, 5), (30, 3)], 110, 14) == [0, 1, 2, 3]


def test_empty_queue_and_single_vessel() -> None:
    assert select_vessels([], 110, 14) == []
    assert select_vessels([(10, 4)], 110, 14) == [0]


def test_a_vessel_that_does_not_fit_stops_the_selection() -> None:
    # The second vessel does not fit next to the first, and the third may not overtake it.
    assert select_vessels([(100, 12), (100, 12), (5, 2)], 110, 14) == [0]


def test_first_vessel_always_enters_even_when_it_fits_nowhere() -> None:
    assert select_vessels([(150, 10), (20, 5)], 110, 14) == [0]
    assert select_vessels([(60, 20), (20, 5)], 110, 14) == [0]


def test_long_queue_does_not_overfill_the_chamber() -> None:
    chosen = select_vessels([(80, 6), (25, 4), (25, 3)] * 20, 110, 14)
    assert chosen == list(range(len(chosen))) and 1 < len(chosen) < 60


def _run_arrivals(lock: Lock, env: simpy.Environment, arrivals):
    """Vessels arrive at (time, side); returns arrival, entry, exit and the estimate."""
    results: dict[str, dict[str, float | bool]] = {}

    def vessel(boat: Boat, at: float, side: str):
        yield env.timeout(at)
        chamber, estimate = lock.best_chamber(side, boat.L, boat.B)
        request = chamber.submit(boat, side)
        yield request.entered
        yield request.exited
        results[boat.name] = {
            "arrival": request.arrival_s,
            "entry": request.entry_s,
            "exit": request.exit_s,
            "estimate": estimate,
            "oversize": request.oversize,
            "chamber": chamber.name,
        }

    for boat, at, side in arrivals:
        env.process(vessel(boat, at, side))
    env.run()
    return results


def _lock(env: simpy.Environment, *chambers: ChamberSpec) -> Lock:
    return Lock(env, LockComplex("1", "Lock", chambers))


def test_queue_is_served_in_turns_from_both_sides() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("c1", 110, 14, 1800))
    arrivals = [
        (Boat("a", 80, 6), 0, "A"),
        (Boat("b", 50, 7), 10, "A"),
        (Boat("c", 50, 5), 20, "B"),
        (Boat("d", 150, 10), 30, "A"),
        (Boat("e", 70, 3), 40, "A"),
    ]
    got = _run_arrivals(lock, env, arrivals)
    # a crosses alone to B. The chamber then takes c from B back to A, then b, d and e in turn.
    assert (got["a"]["entry"], got["a"]["exit"]) == (0, 1800)
    assert (got["c"]["entry"], got["c"]["exit"]) == (1800, 3600)
    assert (got["b"]["entry"], got["b"]["exit"]) == (3600, 5400)
    assert (got["d"]["entry"], got["d"]["exit"]) == (7200, 9000)
    assert (got["e"]["entry"], got["e"]["exit"]) == (10800, 12600)


def test_oversize_vessel_passes_alone_and_is_flagged() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("c1", 110, 14, 1800))
    got = _run_arrivals(lock, env, [(Boat("big", 150, 10), 0, "A"), (Boat("small", 20, 4), 1, "A")])
    assert got["big"]["oversize"] is True and got["small"]["oversize"] is False
    # The small vessel does not ride along, so it waits for the next load.
    assert got["big"]["exit"] <= got["small"]["entry"]


def test_expected_wait_matches_the_simulated_wait_for_a_queue() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("c1", 40, 6, 1800))
    arrivals = [(Boat(f"v{i}", 38, 5), i * 10.0, "A") for i in range(4)]
    got = _run_arrivals(lock, env, arrivals)
    for boat in got.values():
        assert boat["estimate"] == pytest.approx(boat["entry"] - boat["arrival"], abs=1)


def test_vessel_picks_the_chamber_with_the_shortest_wait() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("c1", 40, 6, 1800), ChamberSpec("c2", 40, 6, 1800))
    arrivals = [(Boat(f"v{i}", 38, 5), 0.0, "A") for i in range(2)]
    got = _run_arrivals(lock, env, arrivals)
    assert {got["v0"]["chamber"], got["v1"]["chamber"]} == {"1:c1", "1:c2"}
    assert got["v0"]["entry"] == got["v1"]["entry"] == 0


def test_only_chambers_that_fit_are_used_and_the_largest_for_oversize() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("small", 60, 8, 600), ChamberSpec("large", 110, 14, 1800))
    got = _run_arrivals(
        lock,
        env,
        [(Boat("mid", 90, 10), 0, "A"), (Boat("huge", 200, 30), 0, "A")],
    )
    assert got["mid"]["chamber"] == "1:large"
    assert got["huge"]["chamber"] == "1:large" and got["huge"]["oversize"] is True


def test_idle_chamber_on_the_other_side_costs_one_crossing() -> None:
    env = simpy.Environment()
    lock = _lock(env, ChamberSpec("c1", 110, 14, 1200))
    got = _run_arrivals(lock, env, [(Boat("a", 40, 5), 0, "B")])
    assert got["a"]["estimate"] == 1200 and got["a"]["entry"] == 1200
