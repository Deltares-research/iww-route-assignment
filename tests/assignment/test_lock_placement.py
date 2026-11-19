"""Placing lock complexes on network edges."""

from __future__ import annotations

import pytest
from shapely import LineString

from iww_route_assignment import AssignmentNetwork
from iww_route_assignment.assignment import SIDE_A, SIDE_B, entry_side, place_locks
from iww_route_assignment.domain import ChamberSpec, LockComplex

from .conftest import NODES, make_network

CHAMBER = ChamberSpec("c", 100.0, 10.0, 1800.0)


def _lock(lock_id: str, lon: float | None, lat: float | None) -> LockComplex:
    return LockComplex(lock_id, f"Lock {lock_id}", (CHAMBER,), lon, lat)


def _along(a: str, b: str, fraction: float) -> tuple[float, float]:
    (x1, y1), (x2, y2) = NODES[a], NODES[b]
    return x1 + fraction * (x2 - x1), y1 + fraction * (y2 - y1)


def test_lock_sits_on_the_edge_of_its_section_at_its_position() -> None:
    network = make_network()
    lock = _lock("L", *_along("A", "C", 0.25))
    placed = place_locks(network, {"L": lock}, {"NLAC": ("L",)})
    (placement,) = placed.placements
    assert network.edges[placement.edge_index].arc_id == "A|C"
    assert placement.fraction == pytest.approx(0.25, abs=0.01)
    assert placement.distance_m is not None and placement.distance_m < 50
    assert placed.report["placed"] == 1 and placed.report["edges_with_locks"] == 1


def test_side_follows_the_direction_of_travel() -> None:
    assert entry_side(forward=True) == SIDE_A
    assert entry_side(forward=False) == SIDE_B


def test_closest_edge_wins_when_sections_share_a_code() -> None:
    network = make_network(AB={"section_code": "SHARED"}, BD={"section_code": "SHARED"})
    lock = _lock("L", *_along("B", "D", 0.8))
    placed = place_locks(network, {"L": lock}, {"SHARED": ("L",)})
    (placement,) = placed.placements
    assert network.edges[placement.edge_index].arc_id == "B|D"
    assert placed.report["locks_on_shared_section_code"] == 1


def test_fraction_refers_to_from_node_even_when_the_line_runs_the_other_way() -> None:
    network = make_network()
    lines = list(network.edge_lines)
    lines[2] = LineString([NODES["C"], NODES["A"]])
    reversed_network = AssignmentNetwork(
        network.edges, network.node_ids, network.node_lon_lat, tuple(lines)
    )
    lock = _lock("L", *_along("A", "C", 0.1))
    (placement,) = place_locks(reversed_network, {"L": lock}, {"NLAC": ("L",)}).placements
    assert placement.fraction == pytest.approx(0.1, abs=0.01)


def test_locks_on_one_edge_are_ordered_in_sailing_direction() -> None:
    network = make_network()
    locks = {
        "far": _lock("far", *_along("A", "C", 0.8)),
        "near": _lock("near", *_along("A", "C", 0.2)),
    }
    placed = place_locks(network, locks, {"NLAC": ("far", "near")})
    edge = placed.placements[0].edge_index
    assert [p.lock_id for p in placed.on_edge(edge, forward=True)] == ["near", "far"]
    assert [p.lock_id for p in placed.on_edge(edge, forward=False)] == ["far", "near"]
    assert placed.report["edges_with_several_locks"] == 1


def test_locks_that_cannot_be_placed_are_counted() -> None:
    network = make_network()
    locks = {
        "no_link": _lock("no_link", 5.0, 52.0),
        "no_point": _lock("no_point", None, None),
        "far": _lock("far", 5.5, 52.5),
    }
    links = {"NLAC": ("no_point", "far"), "NLXX": ("no_link",)}
    placed = place_locks(network, locks, links, max_distance_m=1000.0)
    assert [p.lock_id for p in placed.placements] == ["no_point"]
    assert placed.placements[0].fraction == 0.5 and placed.placements[0].distance_m is None
    assert placed.report["locks_without_edge"] == 1
    assert placed.report["locks_without_point"] == 1
    assert placed.report["locks_too_far_from_edge"] == 1
