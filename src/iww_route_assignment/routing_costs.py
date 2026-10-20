"""Voyage-level cost model that connects the cost calculators to route assignment.

Route assignment needs one scalar weight per graph edge for every voyage group.
The cost calculators need a vessel class, cargo appearance, loading ratio and a
fuel rate that depends on the waterway class of each edge. This module resolves
those voyage-level inputs once, caches the fuel rate per waterway class, and
offers the edge weight for the selected :class:`RouteCostMetric` and the priced
route after assignment.

Empty trips are priced like any other trip. They have loading ratio zero, so the
light-loading fuel rate applies, and they carry no loading or unloading cost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .costs.calculator import (
    calculate_arc_choice_time_h,
    calculate_arc_cost,
    calculate_arc_costs,
    calculate_trip_fixed_cost,
    combine_trip_cost,
)
from .costs.fuel import FuelConsumptionProvider
from .domain.classifications import profile_for_appearance
from .domain.enums import RouteCostMetric, VesselProfile
from .domain.models import (
    AppearanceRule,
    ArcCostBreakdown,
    ArcTravelInput,
    CostRates,
    ResolvedFuelRate,
    ScenarioCostParameters,
    TripCostBreakdown,
    TripCostInput,
    TripFixedCostBreakdown,
)
from .exceptions import ValidationError
from .io.loaders import select_cost_rates
from .preparation import DEFAULT_NSTR_APPEARANCE, appearance_for_cargo


@dataclass(frozen=True, slots=True)
class RoutePricing:
    """Reported and route-choice cost of one trip along an assigned route."""

    arcs: tuple[ArcCostBreakdown, ...]
    fixed: TripFixedCostBreakdown
    trip: TripCostBreakdown
    is_loaded: bool
    appearance: str
    appearance_source: str
    loading_ratio: float

    @property
    def fuel_l(self) -> float:
        """Total sailing and idle fuel use in litres."""
        return sum(arc.sailing_fuel_l + arc.idle_fuel_l for arc in self.arcs)

    @property
    def reported_time_h(self) -> float:
        """Total reported travel time in hours."""
        return sum(arc.reported_time_h for arc in self.arcs)


class VoyageCostModel:
    """Edge weights and route pricing for one vessel and cargo combination."""

    def __init__(
        self,
        *,
        metric: RouteCostMetric,
        rates: CostRates,
        scenario: ScenarioCostParameters,
        fuel_provider: FuelConsumptionProvider,
        vessel_rws_class: str,
        vessel_profile: VesselProfile,
        loading_ratio: float,
        trip: TripCostInput,
        appearance_source: str = "unspecified",
    ) -> None:
        self.metric = RouteCostMetric(metric)
        self.rates = rates
        self.scenario = scenario
        self.vessel_rws_class = vessel_rws_class
        self.vessel_profile = vessel_profile
        self.loading_ratio = loading_ratio
        self.trip = trip
        self.appearance_source = appearance_source
        self._fuel_provider = fuel_provider
        self._fuel_cache: dict[str, ResolvedFuelRate] = {}

    def fuel_for(self, waterway_cemt_class: str) -> ResolvedFuelRate:
        """Resolve and cache the fuel rate for one waterway class."""
        fuel = self._fuel_cache.get(waterway_cemt_class)
        if fuel is None:
            fuel = self._fuel_provider.resolve(
                self.vessel_rws_class,
                waterway_cemt_class,
                self.vessel_profile,
                loading_ratio=self.loading_ratio,
            )
            self._fuel_cache[waterway_cemt_class] = fuel
        return fuel

    def edge_weight(self, arc: ArcTravelInput, waterway_cemt_class: str) -> float:
        """Return the quantity minimised by the route search for one edge."""
        if self.metric is RouteCostMetric.TRAVEL_TIME:
            return calculate_arc_choice_time_h(arc)
        fuel = self.fuel_for(waterway_cemt_class)
        return calculate_arc_cost(arc, self.rates, self.scenario, fuel).choice_cost_eur

    def price_route(
        self, arcs: Sequence[ArcTravelInput], waterway_cemt_classes: Sequence[str]
    ) -> RoutePricing:
        """Price one trip along a route, adding the fixed trip cost once."""
        if len(arcs) != len(waterway_cemt_classes):
            raise ValidationError(
                "arcs and waterway_cemt_classes must have equal length; "
                f"received {len(arcs)} and {len(waterway_cemt_classes)}"
            )
        fuels = [self.fuel_for(cemt) for cemt in waterway_cemt_classes]
        breakdowns = calculate_arc_costs(arcs, self.rates, self.scenario, fuels)
        fixed = calculate_trip_fixed_cost(self.trip, self.rates, self.scenario)
        return RoutePricing(
            arcs=breakdowns,
            fixed=fixed,
            trip=combine_trip_cost(breakdowns, fixed),
            is_loaded=self.trip.is_loaded,
            appearance=self.rates.appearance,
            appearance_source=self.appearance_source,
            loading_ratio=self.loading_ratio,
        )


@dataclass(frozen=True, slots=True)
class RoutingCostConfig:
    """Run-level cost inputs shared by every voyage group."""

    metric: RouteCostMetric
    rates: tuple[CostRates, ...]
    fuel_provider: FuelConsumptionProvider
    scenario: ScenarioCostParameters
    appearance_map: Mapping[str, AppearanceRule] = field(
        default_factory=lambda: DEFAULT_NSTR_APPEARANCE
    )

    def voyage_model(
        self,
        *,
        vessel_rws_class: str,
        cargo_code: str,
        avg_cargo_t: float,
        capacity_t: float | None,
    ) -> VoyageCostModel:
        """Build the cost model of one voyage group from its average trip.

        A zero average cargo is an empty trip and is accepted without a capacity.
        """
        avg_cargo_t = float(avg_cargo_t)
        if avg_cargo_t < 0:
            raise ValidationError(f"avg_cargo_t must not be negative; received {avg_cargo_t!r}")
        appearance, appearance_source = appearance_for_cargo(
            cargo_code, avg_cargo_t, 0.0, self.appearance_map
        )
        rates = select_cost_rates(self.rates, appearance, vessel_rws_class)
        return VoyageCostModel(
            metric=self.metric,
            rates=rates,
            scenario=self.scenario,
            fuel_provider=self.fuel_provider,
            vessel_rws_class=vessel_rws_class,
            vessel_profile=profile_for_appearance(appearance),
            loading_ratio=_loading_ratio(avg_cargo_t, capacity_t),
            trip=TripCostInput(cargo_t=avg_cargo_t, is_loaded=avg_cargo_t > 0),
            appearance_source=appearance_source,
        )


def _loading_ratio(cargo_t: float, capacity_t: float | None) -> float:
    if cargo_t <= 0:
        return 0.0
    if capacity_t is None or not capacity_t > 0:
        raise ValidationError(
            f"capacity_t must be positive for a loaded trip; received {capacity_t!r}"
        )
    return min(cargo_t / capacity_t, 1.0)
