"""Malformed, missing, duplicate, non-finite and boundary inputs to the assignment contracts."""

from __future__ import annotations

import numpy as np
import pytest

from iww_route_assignment import (
    AssignmentNetwork,
    AssignmentSettings,
    NavigabilityPenalties,
    NetworkEdge,
    RoutePreferences,
    ValidationError,
    VesselCatalogue,
)
from iww_route_assignment.assignment import (
    CostInputs,
    check_cost_coverage,
    routing_loading_ratio,
    search_weights,
)
from iww_route_assignment.domain.enums import RouteCostMetric

from .conftest import NODES, make_network, make_rates


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("distance_km", float("nan")),
        ("distance_km", -1.0),
        ("speed_km_per_h", 0.0),
        ("lock_service_time_h", float("inf")),
        ("arc_id", " "),
        ("waterway_cemt_class", "XI"),
    ],
)
def test_edge_rejects_bad_values(field: str, value: object) -> None:
    values = {
        "arc_id": "a",
        "from_node": "A",
        "to_node": "B",
        "distance_km": 1.0,
        "speed_km_per_h": 10.0,
    } | {field: value}
    with pytest.raises(ValidationError):
        NetworkEdge(**values)  # type: ignore[arg-type]


def test_network_rejects_inconsistent_inputs() -> None:
    edge = NetworkEdge("A|B", "A", "B", 1.0, 10.0)
    coords = np.array(list(NODES.values()))
    with pytest.raises(ValidationError, match="no edges"):
        AssignmentNetwork((), tuple(NODES), coords, ())
    with pytest.raises(ValidationError, match="unknown node"):
        AssignmentNetwork((NetworkEdge("A|X", "A", "X", 1.0, 10.0),), tuple(NODES), coords, (None,))
    with pytest.raises(ValidationError, match="Parallel"):
        AssignmentNetwork(
            (edge, NetworkEdge("B|A", "B", "A", 1.0, 10.0)), tuple(NODES), coords, (None, None)
        )
    with pytest.raises(ValidationError, match="unique"):
        AssignmentNetwork((edge, edge), tuple(NODES), coords, (None, None))
    with pytest.raises(ValidationError, match="finite"):
        AssignmentNetwork((edge,), tuple(NODES), coords * np.nan, (None,))


def test_network_search_matrix_and_components() -> None:
    network = make_network()
    assert network.component_count() == 1
    with pytest.raises(ValidationError):
        network.search_matrix(np.array([1.0, -1.0, 1.0, 1.0]))
    with pytest.raises(ValidationError):
        network.search_matrix(np.ones(3))
    # A zero weight stays an edge, so a zero-length arc can still be used.
    assert network.search_matrix(np.zeros(4)).nnz == 8


@pytest.mark.parametrize(
    "options",
    [
        {"routing_ratio_step": 0.0},
        {"routing_ratio_step": 1.5},
        {"max_snap_distance_m": -1.0},
        {"n_workers": 0},
        {"origins_per_search": True},
        {"origin_column": ""},
        {"metric": "fastest"},
    ],
)
def test_settings_reject_bad_values(options: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        AssignmentSettings(**options)  # type: ignore[arg-type]


def test_penalties_and_preferences_reject_negative_hours() -> None:
    with pytest.raises(ValidationError):
        NavigabilityPenalties(beam_h=-1.0)
    with pytest.raises(ValidationError):
        RoutePreferences(sea_h=float("nan"))
    assert NavigabilityPenalties(flat_beam_countries=frozenset({" nl "})).flat_beam_countries == {
        "NL"
    }


@pytest.mark.parametrize(
    ("ratio", "step", "expected"),
    [(0.0, 0.05, 0.0), (0.01, 0.05, 0.05), (0.512, 0.05, 0.5), (0.99, 0.05, 1.0), (0.3, None, 0.3)],
)
def test_routing_ratio_rounding_boundaries(
    ratio: float, step: float | None, expected: float
) -> None:
    assert routing_loading_ratio(ratio, step) == pytest.approx(expected)


def test_cost_inputs_reject_duplicates_and_report_gaps(cost_inputs, catalogue) -> None:
    provider = cost_inputs.fuel_provider
    with pytest.raises(ValidationError, match="Duplicate"):
        CostInputs((make_rates(), make_rates()), provider, cost_inputs.scenario)
    with pytest.raises(ValidationError, match="At least one"):
        CostInputs((), provider, cost_inputs.scenario)
    report = check_cost_coverage(
        [("M1", "droge_bulk"), ("M1", "container"), ("M9", "overig")],
        cost_inputs,
        catalogue.by_class,
    )
    assert report.missing_vessels == ("M9",)
    assert report.missing_rates == (("M1", "container"), ("M9", "overig"))
    assert not report.complete
    with pytest.raises(ValidationError, match="No cost rates"):
        cost_inputs.rates_for("M2", "droge_bulk")


def test_cost_metric_needs_cost_inputs(network) -> None:
    from iww_route_assignment.assignment import edge_travel

    with pytest.raises(ValidationError, match="needs rates"):
        search_weights(RouteCostMetric.TRAVEL_COST_EUR, edge_travel(network), np.zeros(4))


def test_catalogue_rejects_duplicates_and_resolves_states(catalogue) -> None:
    from iww_route_assignment import NavigabilityVessel

    with pytest.raises(ValidationError, match="Duplicate vessel reference"):
        VesselCatalogue(catalogue.references * 2)
    state = NavigabilityVessel("M1-loaded", 38.5, 5.05, 2.2, air_draught_m=4.3)
    with_states = VesselCatalogue(catalogue.references, (state,))
    assert with_states.navigability_vessel("M1", True).air_draught_m == 4.3
    assert with_states.navigability_vessel("M1", False).draught_m == 1.0
    assert with_states.resolve_class(" ") is None
    with pytest.raises(ValidationError, match="Duplicate navigability"):
        VesselCatalogue(catalogue.references, (state, state))
