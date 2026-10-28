"""Pure BIVAS-style monetary cost calculations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True, eq=False)
class ArcCostArrays:
    """Arc cost components as aligned arrays, one element per arc.

    This is the numerical core of :func:`calculate_arc_costs`. Route search uses it
    directly, so search weights and reported costs come from the same formula.
    """

    movement_time_h: npt.NDArray[np.float64]
    reported_fixed_time_h: npt.NDArray[np.float64]
    reported_time_h: npt.NDArray[np.float64]
    choice_only_time_h: npt.NDArray[np.float64]
    choice_time_h: npt.NDArray[np.float64]
    distance_cost_eur: npt.NDArray[np.float64]
    labour_cost_eur: npt.NDArray[np.float64]
    maintenance_time_cost_eur: npt.NDArray[np.float64]
    insurance_cost_eur: npt.NDArray[np.float64]
    interest_cost_eur: npt.NDArray[np.float64]
    depreciation_cost_eur: npt.NDArray[np.float64]
    port_dues_cost_eur: npt.NDArray[np.float64]
    other_cost_eur: npt.NDArray[np.float64]
    time_cost_eur: npt.NDArray[np.float64]
    sailing_fuel_l: npt.NDArray[np.float64]
    idle_fuel_l: npt.NDArray[np.float64]
    sailing_fuel_cost_eur: npt.NDArray[np.float64]
    idle_fuel_cost_eur: npt.NDArray[np.float64]
    reported_variable_cost_eur: npt.NDArray[np.float64]
    choice_only_cost_eur: npt.NDArray[np.float64]
    choice_cost_eur: npt.NDArray[np.float64]


def calculate_arc_cost_arrays(
    distance_km: npt.ArrayLike,
    realised_speed_km_per_h: npt.ArrayLike,
    reported_fixed_time_h: npt.ArrayLike,
    choice_only_time_h: npt.ArrayLike,
    fuel_rate_l_per_h: npt.ArrayLike,
    rates: CostRates,
    scenario: ScenarioCostParameters,
) -> ArcCostArrays:
    """Calculate arc costs from aligned arrays without building one object per arc.

    ``reported_fixed_time_h`` is lock service, lock waiting, bridge service and real
    delay. ``choice_only_time_h`` is calibration delay and relaxation penalty.
    """
    distance = _checked("distance_km", distance_km)
    speed = _checked("realised_speed_km_per_h", realised_speed_km_per_h)
    reported_fixed_time = _checked("reported_fixed_time_h", reported_fixed_time_h)
    choice_only_time = _checked("choice_only_time_h", choice_only_time_h)
    fuel_rate = _checked("fuel_rate_l_per_h", fuel_rate_l_per_h)
    shapes = {a.shape for a in (distance, speed, reported_fixed_time, choice_only_time, fuel_rate)}
    if len(shapes) != 1:
        raise ValidationError(f"Arc cost arrays must have equal shapes; received {shapes}")
    if np.any(speed <= 0):
        raise ValidationError("realised_speed_km_per_h must be positive")

    movement_time = distance / speed
    reported_time = movement_time + reported_fixed_time
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

    sailing_fuel = fuel_rate * movement_time
    idle_fuel = fuel_rate * scenario.idle_fuel_factor * reported_fixed_time
    sailing_fuel_cost = sailing_fuel * scenario.fuel_price_eur_per_l
    idle_fuel_cost = idle_fuel * scenario.fuel_price_eur_per_l
    reported_variable_cost = distance_cost + time_cost + sailing_fuel_cost + idle_fuel_cost

    choice_only_cost = hourly_rate_eur_per_h(rates, scenario) * choice_only_time
    return ArcCostArrays(
        movement_time_h=movement_time,
        reported_fixed_time_h=reported_fixed_time,
        reported_time_h=reported_time,
        choice_only_time_h=choice_only_time,
        choice_time_h=choice_time,
        distance_cost_eur=distance_cost,
        labour_cost_eur=labour_cost,
        maintenance_time_cost_eur=maintenance_time_cost,
        insurance_cost_eur=insurance_cost,
        interest_cost_eur=interest_cost,
        depreciation_cost_eur=depreciation_cost,
        port_dues_cost_eur=port_dues_cost,
        other_cost_eur=other_cost,
        time_cost_eur=time_cost,
        sailing_fuel_l=sailing_fuel,
        idle_fuel_l=idle_fuel,
        sailing_fuel_cost_eur=sailing_fuel_cost,
        idle_fuel_cost_eur=idle_fuel_cost,
        reported_variable_cost_eur=reported_variable_cost,
        choice_only_cost_eur=choice_only_cost,
        choice_cost_eur=reported_variable_cost + choice_only_cost,
    )


def hourly_rate_eur_per_h(rates: CostRates, scenario: ScenarioCostParameters) -> float:
    """Return the time-dependent hourly rate that prices choice-only time."""
    return (
        rates.labour_eur_per_h
        + rates.maintenance_eur_per_h
        + rates.insurance_eur_per_h
        + scenario.interest_rate_percent * rates.interest_eur_per_percent_h
        + rates.depreciation_eur_per_h
        + rates.port_dues_eur_per_h
        + rates.other_eur_per_h
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

    reported_fixed_time = np.zeros(len(arcs), dtype=np.float64)
    for name in (
        "lock_service_time_h",
        "lock_waiting_time_h",
        "bridge_service_time_h",
        "real_delay_time_h",
    ):
        reported_fixed_time += _array(arcs, name)
    costs = calculate_arc_cost_arrays(
        _array(arcs, "distance_km"),
        _array(arcs, "realised_speed_km_per_h"),
        reported_fixed_time,
        _array(arcs, "calibration_delay_time_h") + _array(arcs, "relaxation_penalty_time_h"),
        np.asarray([fuel.fuel_rate_l_per_h for fuel in fuels], dtype=np.float64),
        rates,
        scenario,
    )
    names = [name for name in ArcCostArrays.__slots__ if not name.startswith("__")]
    columns = [getattr(costs, name).tolist() for name in names]
    return tuple(
        ArcCostBreakdown(**dict(zip(names, values, strict=True)), fuel=fuels[index])
        for index, values in enumerate(zip(*columns, strict=True))
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


def combine_trip_cost_arrays(
    arcs: ArcCostArrays, fixed: TripFixedCostBreakdown
) -> TripCostBreakdown:
    """Array form of :func:`combine_trip_cost`: sum arcs, add the fixed cost once."""
    arc_reported = float(arcs.reported_variable_cost_eur.sum())
    arc_choice = float(arcs.choice_cost_eur.sum())
    return TripCostBreakdown(
        arc_reported_cost_eur=arc_reported,
        fixed_cost_eur=fixed.total_fixed_cost_eur,
        reported_total_cost_eur=arc_reported + fixed.total_fixed_cost_eur,
        arc_choice_cost_eur=arc_choice,
        choice_total_cost_eur=arc_choice + fixed.total_fixed_cost_eur,
    )


def _checked(name: str, values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(array)) or np.any(array < 0):
        raise ValidationError(f"{name} must be finite and non-negative")
    return array


def _array(arcs: Sequence[ArcTravelInput], name: str) -> npt.NDArray[np.float64]:
    return np.fromiter(
        (float(getattr(arc, name)) for arc in arcs),
        dtype=np.float64,
        count=len(arcs),
    )
