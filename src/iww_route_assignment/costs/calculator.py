"""Pure BIVAS-style monetary cost calculations."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from ..domain.models import (
    ArcCostBreakdown,
    ArcTravelInput,
    CostRates,
    ResolvedFuelRate,
    ScenarioCostParameters,
    TripCostBreakdown,
    TripCostInput,
    TripFixedCostBreakdown,
)
from ..exceptions import ValidationError


def calculate_arc_cost(
    arc: ArcTravelInput,
    rates: CostRates,
    scenario: ScenarioCostParameters,
    fuel: ResolvedFuelRate,
) -> ArcCostBreakdown:
    """Calculate reported and route-choice cost for one directed arc."""
    return calculate_arc_costs((arc,), rates, scenario, (fuel,))[0]


def calculate_arc_choice_time_h(arc: ArcTravelInput) -> float:
    """Return the route-choice time of one arc without needing rates or fuel.

    The value equals ``ArcCostBreakdown.choice_time_h``: movement time, reported
    fixed times and the choice-only calibration and relaxation times.
    """
    movement_time = float(arc.distance_km) / float(arc.realised_speed_km_per_h)
    return (
        movement_time
        + arc.lock_service_time_h
        + arc.lock_waiting_time_h
        + arc.bridge_service_time_h
        + arc.real_delay_time_h
        + arc.calibration_delay_time_h
        + arc.relaxation_penalty_time_h
    )


def calculate_arc_costs(
    arcs: Sequence[ArcTravelInput],
    rates: CostRates,
    scenario: ScenarioCostParameters,
    fuels: Sequence[ResolvedFuelRate],
) -> tuple[ArcCostBreakdown, ...]:
    """Calculate a vectorised batch equivalent to repeated scalar calls."""
    if len(arcs) != len(fuels):
        raise ValidationError(
            f"arcs and fuels must have equal length; received {len(arcs)} and {len(fuels)}"
        )
    if not arcs:
        return ()

    distance = _array(arcs, "distance_km")
    speed = _array(arcs, "realised_speed_km_per_h")
    movement_time = distance / speed
    reported_fixed_time = np.zeros(len(arcs), dtype=np.float64)
    for name in (
        "lock_service_time_h",
        "lock_waiting_time_h",
        "bridge_service_time_h",
        "real_delay_time_h",
    ):
        reported_fixed_time += _array(arcs, name)
    reported_time = movement_time + reported_fixed_time
    choice_only_time = _array(arcs, "calibration_delay_time_h") + _array(
        arcs, "relaxation_penalty_time_h"
    )
    choice_time = reported_time + choice_only_time

    distance_cost = rates.maintenance_eur_per_km * distance
    labour_cost = rates.labour_eur_per_h * reported_time
    maintenance_time_cost = rates.maintenance_eur_per_h * reported_time
    insurance_cost = rates.insurance_eur_per_h * reported_time
    interest_cost = (
        scenario.interest_rate_percent * rates.interest_eur_per_percent_h * reported_time
    )
    depreciation_cost = rates.depreciation_eur_per_h * reported_time
    port_dues_cost = rates.port_dues_eur_per_h * reported_time
    other_cost = rates.other_eur_per_h * reported_time
    time_cost = (
        labour_cost
        + maintenance_time_cost
        + insurance_cost
        + interest_cost
        + depreciation_cost
        + port_dues_cost
        + other_cost
    )

    fuel_rate = np.asarray([fuel.fuel_rate_l_per_h for fuel in fuels], dtype=np.float64)
    sailing_fuel = fuel_rate * movement_time
    idle_fuel = fuel_rate * scenario.idle_fuel_factor * reported_fixed_time
    sailing_fuel_cost = sailing_fuel * scenario.fuel_price_eur_per_l
    idle_fuel_cost = idle_fuel * scenario.fuel_price_eur_per_l
    reported_variable_cost = distance_cost + time_cost + sailing_fuel_cost + idle_fuel_cost

    hourly_rate = (
        rates.labour_eur_per_h
        + rates.maintenance_eur_per_h
        + rates.insurance_eur_per_h
        + scenario.interest_rate_percent * rates.interest_eur_per_percent_h
        + rates.depreciation_eur_per_h
        + rates.port_dues_eur_per_h
        + rates.other_eur_per_h
    )
    choice_only_cost = hourly_rate * choice_only_time
    choice_cost = reported_variable_cost + choice_only_cost

    return tuple(
        ArcCostBreakdown(
            movement_time_h=float(movement_time[index]),
            reported_fixed_time_h=float(reported_fixed_time[index]),
            reported_time_h=float(reported_time[index]),
            choice_only_time_h=float(choice_only_time[index]),
            choice_time_h=float(choice_time[index]),
            distance_cost_eur=float(distance_cost[index]),
            labour_cost_eur=float(labour_cost[index]),
            maintenance_time_cost_eur=float(maintenance_time_cost[index]),
            insurance_cost_eur=float(insurance_cost[index]),
            interest_cost_eur=float(interest_cost[index]),
            depreciation_cost_eur=float(depreciation_cost[index]),
            port_dues_cost_eur=float(port_dues_cost[index]),
            other_cost_eur=float(other_cost[index]),
            time_cost_eur=float(time_cost[index]),
            sailing_fuel_l=float(sailing_fuel[index]),
            idle_fuel_l=float(idle_fuel[index]),
            sailing_fuel_cost_eur=float(sailing_fuel_cost[index]),
            idle_fuel_cost_eur=float(idle_fuel_cost[index]),
            reported_variable_cost_eur=float(reported_variable_cost[index]),
            choice_only_cost_eur=float(choice_only_cost[index]),
            choice_cost_eur=float(choice_cost[index]),
            fuel=fuels[index],
        )
        for index in range(len(arcs))
    )


def calculate_trip_fixed_cost(
    trip: TripCostInput,
    rates: CostRates,
    scenario: ScenarioCostParameters,
) -> TripFixedCostBreakdown:
    """Calculate route-independent costs, which apply to loaded trips only."""
    if not trip.is_loaded:
        return TripFixedCostBreakdown(0.0, 0.0, 0.0, 0.0)
    loading = rates.loading_eur_per_t * trip.cargo_t
    unloading = rates.unloading_eur_per_t * trip.cargo_t
    waiting = rates.labour_waiting_eur_per_h * scenario.cargo_waiting_time_h
    return TripFixedCostBreakdown(
        loading_cost_eur=loading,
        unloading_cost_eur=unloading,
        cargo_waiting_cost_eur=waiting,
        total_fixed_cost_eur=loading + unloading + waiting,
    )


def combine_trip_cost(
    arcs: Sequence[ArcCostBreakdown], fixed: TripFixedCostBreakdown
) -> TripCostBreakdown:
    """Add a fixed trip cost once after summing route arcs."""
    arc_reported = sum(arc.reported_variable_cost_eur for arc in arcs)
    arc_choice = sum(arc.choice_cost_eur for arc in arcs)
    return TripCostBreakdown(
        arc_reported_cost_eur=arc_reported,
        fixed_cost_eur=fixed.total_fixed_cost_eur,
        reported_total_cost_eur=arc_reported + fixed.total_fixed_cost_eur,
        arc_choice_cost_eur=arc_choice,
        choice_total_cost_eur=arc_choice + fixed.total_fixed_cost_eur,
    )


def _array(arcs: Sequence[ArcTravelInput], name: str) -> npt.NDArray[np.float64]:
    return np.fromiter(
        (float(getattr(arc, name)) for arc in arcs),
        dtype=np.float64,
        count=len(arcs),
    )
