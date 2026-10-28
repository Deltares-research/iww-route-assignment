"""Connect the assignment to the cost function.

Route search and reported cost use the same formula, :func:`calculate_arc_cost_arrays`.
The search may use a rounded loading ratio. Reported cost always uses the exact one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from ..costs import FuelConsumptionProvider
from ..costs.calculator import (
    ArcCostArrays,
    calculate_arc_cost_arrays,
    calculate_trip_fixed_cost,
    combine_trip_cost_arrays,
)
from ..domain.classifications import normalise_rws_class
from ..domain.enums import RouteCostMetric
from ..domain.models import (
    CostRates,
    ResolvedFuelRate,
    ScenarioCostParameters,
    TripCostBreakdown,
    TripCostInput,
    TripFixedCostBreakdown,
)
from ..exceptions import ValidationError


@dataclass(frozen=True, slots=True, eq=False)
class CostInputs:
    """Cost rates, fuel provider and scenario shared by every trip."""

    rates: tuple[CostRates, ...]
    fuel_provider: FuelConsumptionProvider
    scenario: ScenarioCostParameters
    rate_index: Mapping[tuple[str, str], CostRates] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        index: dict[tuple[str, str], CostRates] = {}
        for rate in self.rates:
            key = (rate.vessel_rws_class.casefold(), rate.appearance.casefold())
            if key in index:
                raise ValidationError(f"Duplicate cost-rate key {key!r}")
            index[key] = rate
        if not index:
            raise ValidationError("At least one cost-rate row is required")
        object.__setattr__(self, "rate_index", index)

    def rates_for(self, vessel_rws_class: str, appearance: str) -> CostRates:
        """Return the one cost-rate row for a vessel class and appearance."""
        key = (normalise_rws_class(vessel_rws_class).casefold(), appearance.strip().casefold())
        rate = self.rate_index.get(key)
        if rate is None:
            raise ValidationError(
                f"No cost rates for vessel_rws_class={vessel_rws_class!r}, "
                f"appearance={appearance!r}"
            )
        return rate

    def has_rates(self, vessel_rws_class: str, appearance: str) -> bool:
        key = (normalise_rws_class(vessel_rws_class).casefold(), appearance.strip().casefold())
        return key in self.rate_index


@dataclass(frozen=True, slots=True)
class CostCoverageReport:
    """Vessel classes and (class, appearance) pairs the reference tables do not cover."""

    missing_vessels: tuple[str, ...]
    missing_rates: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return not self.missing_vessels and not self.missing_rates


def check_cost_coverage(
    pairs: Iterable[tuple[str, str]],
    cost_inputs: CostInputs,
    known_vessels: Iterable[str],
) -> CostCoverageReport:
    """List what is missing before routing, so gaps are reported and never guessed."""
    known = {normalise_rws_class(vessel) for vessel in known_vessels}
    unique = sorted(set(pairs))
    return CostCoverageReport(
        tuple(sorted({vessel for vessel, _ in unique if vessel not in known})),
        tuple(
            (vessel, appearance)
            for vessel, appearance in unique
            if not cost_inputs.has_rates(vessel, appearance)
        ),
    )


def routing_loading_ratio(loading_ratio: float, step: float | None) -> float:
    """Round a loaded ratio to a multiple of ``step`` for route search. Empty stays 0."""
    if step is None or loading_ratio <= 0:
        return loading_ratio
    return min(1.0, max(step, round(loading_ratio / step) * step))


class FuelRates:
    """Resolve and cache fuel rates per vessel class, waterway class and loading ratio."""

    def __init__(self, provider: FuelConsumptionProvider) -> None:
        self._provider = provider
        self._cache: dict[tuple[str, str, float], ResolvedFuelRate] = {}

    def resolve(
        self, vessel: str, waterway_cemt_class: str, loading_ratio: float
    ) -> ResolvedFuelRate:
        key = (vessel, waterway_cemt_class, loading_ratio)
        fuel = self._cache.get(key)
        if fuel is None:
            fuel = self._provider.resolve(vessel, waterway_cemt_class, loading_ratio=loading_ratio)
            self._cache[key] = fuel
        return fuel

    def for_edges(
        self, vessel: str, edge_cemt_classes: Iterable[str], loading_ratio: float
    ) -> tuple[ResolvedFuelRate, ...]:
        return tuple(self.resolve(vessel, cemt, loading_ratio) for cemt in edge_cemt_classes)


@dataclass(frozen=True, slots=True, eq=False)
class EdgeTravel:
    """Travel inputs per edge, aligned with the network edges."""

    distance_km: npt.NDArray[np.float64]
    speed_km_per_h: npt.NDArray[np.float64]
    reported_fixed_time_h: npt.NDArray[np.float64]


def search_weights(
    metric: RouteCostMetric,
    travel: EdgeTravel,
    penalty_h: npt.NDArray[np.float64],
    *,
    rates: CostRates | None = None,
    scenario: ScenarioCostParameters | None = None,
    fuel_rate_l_per_h: npt.NDArray[np.float64] | None = None,
) -> npt.NDArray[np.float64]:
    """Return the quantity the route search minimises on every edge.

    ``travel_time``: movement time plus fixed time plus penalty hours.
    ``travel_cost_eur``: the cost function's ``choice_cost_eur``, where penalty hours are
    priced at the vessel's hourly rate.
    """
    if metric is RouteCostMetric.TRAVEL_TIME:
        return travel.distance_km / travel.speed_km_per_h + travel.reported_fixed_time_h + penalty_h
    if rates is None or scenario is None or fuel_rate_l_per_h is None:
        raise ValidationError("travel_cost_eur needs rates, scenario and fuel rates")
    return calculate_arc_cost_arrays(
        travel.distance_km,
        travel.speed_km_per_h,
        travel.reported_fixed_time_h,
        penalty_h,
        fuel_rate_l_per_h,
        rates,
        scenario,
    ).choice_cost_eur


@dataclass(frozen=True, slots=True, eq=False)
class RoutePrice:
    """Reported and choice cost of one trip along its chosen route."""

    arcs: ArcCostArrays
    fuels: tuple[ResolvedFuelRate, ...]
    fixed: TripFixedCostBreakdown
    trip: TripCostBreakdown


def price_route(
    edges: npt.NDArray[np.int64],
    travel: EdgeTravel,
    penalty_h: npt.NDArray[np.float64],
    fuels: tuple[ResolvedFuelRate, ...],
    rates: CostRates,
    scenario: ScenarioCostParameters,
    trip: TripCostInput,
) -> RoutePrice:
    """Price a route with the exact load. The fixed trip cost is added once."""
    if len(fuels) != len(edges):
        raise ValidationError("fuels must align with route edges")
    arcs = calculate_arc_cost_arrays(
        travel.distance_km[edges],
        travel.speed_km_per_h[edges],
        travel.reported_fixed_time_h[edges],
        penalty_h[edges],
        np.array([fuel.fuel_rate_l_per_h for fuel in fuels], dtype=np.float64),
        rates,
        scenario,
    )
    fixed = calculate_trip_fixed_cost(trip, rates, scenario)
    return RoutePrice(arcs, fuels, fixed, combine_trip_cost_arrays(arcs, fixed))
