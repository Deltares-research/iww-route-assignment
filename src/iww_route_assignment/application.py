"""Cost complete supplied trip sequences. No files, network search or navigability."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .costs import (
    FuelConsumptionProvider,
    calculate_arc_cost,
    calculate_trip_fixed_cost,
    combine_trip_cost,
)
from .domain.arcs import TIME_FIELDS
from .domain.models import (
    ArcCostBreakdown,
    ArcCostRequest,
    ArcTravelInput,
    CostRates,
    ScenarioCostParameters,
    TripCostBreakdown,
    TripCostInput,
    TripFixedCostBreakdown,
)
from .exceptions import ValidationError


@dataclass(frozen=True, slots=True)
class CostedArc:
    request: ArcCostRequest
    cost: ArcCostBreakdown


@dataclass(frozen=True, slots=True)
class CostedTrip:
    source_row_id: str
    route_id: str
    arcs: tuple[CostedArc, ...]
    fixed: TripFixedCostBreakdown
    cost: TripCostBreakdown
    cost_rate_source: str


def cost_trip_requests(
    requests: Iterable[ArcCostRequest],
    rates: Iterable[CostRates],
    scenario: ScenarioCostParameters,
    fuel_provider: FuelConsumptionProvider,
) -> tuple[CostedTrip, ...]:
    """Validate complete sequences, then cost each trip, adding fixed cost once.

    Any missing reference or partial sequence fails the call before a result is
    returned. Callers must not publish partial trips as complete trip totals.
    """
    rate_index = {}
    for rate in rates:
        key = (rate.appearance.casefold(), rate.vessel_rws_class.casefold())
        if key in rate_index:
            raise ValidationError(f"Duplicate cost-rate key {key!r}")
        rate_index[key] = rate
    groups: dict[str, list[ArcCostRequest]] = defaultdict(list)
    seen: set[str] = set()
    for request in requests:
        if request.case_row_id in seen:
            raise ValidationError(f"Duplicate case_row_id {request.case_row_id!r}")
        seen.add(request.case_row_id)
        groups[request.source_row_id].append(request)
    if not groups:
        raise ValidationError("No arc requests to cost")
    results = []
    for trip_id, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda row: row.sequence)
        first = ordered[0]
        if [row.sequence for row in ordered] != list(range(first.route_arc_count)):
            raise ValidationError(f"Incomplete or duplicate arc sequence for trip {trip_id!r}")
        for row in ordered:
            for name in (
                "route_id",
                "route_arc_count",
                "appearance",
                "vessel_rws_class",
                "cargo_t",
                "is_loaded",
                "loading_ratio",
            ):
                if getattr(row, name) != getattr(first, name):
                    raise ValidationError(f"Inconsistent {name} for trip {trip_id!r}")
            if not row.route_id or not row.from_node_id or not row.to_node_id:
                raise ValidationError("Requests require route_id and directed endpoint identities")
        for previous, following in zip(ordered, ordered[1:], strict=False):
            if previous.to_node_id != following.from_node_id:
                raise ValidationError(f"Disconnected directed sequence for trip {trip_id!r}")
        key = (first.appearance.casefold(), first.vessel_rws_class.casefold())
        if key not in rate_index:
            raise ValidationError(f"Missing cost-rate key {key!r} for trip {trip_id!r}")
        rate = rate_index[key]
        costed = []
        for row in ordered:
            fuel = fuel_provider.resolve(
                row.vessel_rws_class, row.waterway_cemt_class, loading_ratio=row.loading_ratio
            )
            travel = ArcTravelInput(
                row.distance_km,
                row.realised_speed_km_per_h,
                **{name: getattr(row, name) for name in TIME_FIELDS},
            )
            costed.append(CostedArc(row, calculate_arc_cost(travel, rate, scenario, fuel)))
        fixed = calculate_trip_fixed_cost(
            TripCostInput(first.cargo_t, first.is_loaded), rate, scenario
        )
        total = combine_trip_cost(tuple(row.cost for row in costed), fixed)
        results.append(
            CostedTrip(trip_id, first.route_id, tuple(costed), fixed, total, rate.source)
        )
    return tuple(results)
