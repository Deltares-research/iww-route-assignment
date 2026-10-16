from dataclasses import asdict

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from iww_route_assignment import (
    ArcTravelInput,
    LoadingCategory,
    TripCostInput,
    ValidationError,
    VesselProfile,
    calculate_arc_cost,
    calculate_arc_costs,
    calculate_trip_fixed_cost,
    combine_trip_cost,
)


def test_arc_cost_returns_every_reported_and_choice_component(
    cost_rates, scenario, fuel_provider
) -> None:
    fuel = fuel_provider.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    arc = ArcTravelInput(
        distance_km=20.0,
        realised_speed_km_per_h=10.0,
        lock_service_time_h=0.5,
        lock_waiting_time_h=0.25,
        bridge_service_time_h=0.1,
        real_delay_time_h=0.15,
        calibration_delay_time_h=0.2,
        relaxation_penalty_time_h=0.3,
    )
    result = calculate_arc_cost(arc, cost_rates, scenario, fuel)

    assert result.movement_time_h == 2.0
    assert result.reported_fixed_time_h == 1.0
    assert result.reported_time_h == 3.0
    assert result.choice_only_time_h == 0.5
    assert result.choice_time_h == 3.5
    assert result.distance_cost_eur == 60.0
    assert result.labour_cost_eur == 30.0
    assert result.maintenance_time_cost_eur == 6.0
    assert result.insurance_cost_eur == 3.0
    assert result.interest_cost_eur == 6.0
    assert result.depreciation_cost_eur == 12.0
    assert result.port_dues_cost_eur == 15.0
    assert result.other_cost_eur == 18.0
    assert result.time_cost_eur == 90.0
    assert result.sailing_fuel_l == 80.0
    assert result.idle_fuel_l == 4.0
    assert result.sailing_fuel_cost_eur == 160.0
    assert result.idle_fuel_cost_eur == 8.0
    assert result.reported_variable_cost_eur == 318.0
    assert result.choice_only_cost_eur == 15.0
    assert result.choice_cost_eur == 333.0


def test_interest_rate_is_in_percentage_points(cost_rates, fuel_provider) -> None:
    from iww_route_assignment import ScenarioCostParameters

    scenario = ScenarioCostParameters(0.0, 3.0, 0.0)
    fuel = fuel_provider.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.0)
    result = calculate_arc_cost(ArcTravelInput(10.0, 10.0), cost_rates, scenario, fuel)
    assert result.interest_cost_eur == pytest.approx(1.5)


def test_trip_fixed_cost_applies_once_to_loaded_trip(cost_rates, scenario) -> None:
    result = calculate_trip_fixed_cost(TripCostInput(100.0, True), cost_rates, scenario)
    assert result.loading_cost_eur == 200.0
    assert result.unloading_cost_eur == 300.0
    assert result.cargo_waiting_cost_eur == 14.0
    assert result.total_fixed_cost_eur == 514.0


def test_empty_trip_has_no_fixed_cost(cost_rates, scenario) -> None:
    result = calculate_trip_fixed_cost(TripCostInput(0.0, False), cost_rates, scenario)
    assert result.total_fixed_cost_eur == 0.0


def test_zero_distance_keeps_reported_fixed_time_cost(cost_rates, scenario, fuel_provider) -> None:
    fuel = fuel_provider.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.0)
    result = calculate_arc_cost(
        ArcTravelInput(0.0, 10.0, lock_waiting_time_h=1.0),
        cost_rates,
        scenario,
        fuel,
    )
    assert result.movement_time_h == 0.0
    assert result.distance_cost_eur == 0.0
    assert result.reported_time_h == 1.0
    assert result.idle_fuel_l == 4.0


def test_trip_total_adds_fixed_cost_once(cost_rates, scenario, fuel_provider) -> None:
    fuel = fuel_provider.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.0)
    arc = calculate_arc_cost(ArcTravelInput(10.0, 10.0), cost_rates, scenario, fuel)
    fixed = calculate_trip_fixed_cost(TripCostInput(100.0, True), cost_rates, scenario)
    result = combine_trip_cost((arc, arc), fixed)
    assert result.arc_reported_cost_eur == 2 * arc.reported_variable_cost_eur
    assert result.reported_total_cost_eur == result.arc_reported_cost_eur + 514.0
    assert result.choice_total_cost_eur == result.arc_choice_cost_eur + 514.0


def test_batch_matches_scalar(cost_rates, scenario, fuel_provider) -> None:
    arcs = (
        ArcTravelInput(10.0, 10.0),
        ArcTravelInput(0.0, 8.0, lock_waiting_time_h=0.25),
        ArcTravelInput(35.0, 12.0, calibration_delay_time_h=0.1),
    )
    fuels = tuple(
        fuel_provider.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=ratio)
        for ratio in (0.0, 0.5, 1.0)
    )
    batch = calculate_arc_costs(arcs, cost_rates, scenario, fuels)
    scalar = tuple(
        calculate_arc_cost(arc, cost_rates, scenario, fuel)
        for arc, fuel in zip(arcs, fuels, strict=True)
    )
    assert [asdict(result) for result in batch] == [asdict(result) for result in scalar]


def test_batch_rejects_mismatched_lengths(cost_rates, scenario) -> None:
    with pytest.raises(ValidationError, match="equal length"):
        calculate_arc_costs((ArcTravelInput(1.0, 1.0),), cost_rates, scenario, ())


def test_empty_batch_is_supported(cost_rates, scenario) -> None:
    assert calculate_arc_costs((), cost_rates, scenario, ()) == ()


@given(
    first=st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False),
    increment=st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_reported_cost_is_monotonic_in_distance(
    first: float, increment: float, cost_rates, scenario, fuel_provider
) -> None:
    fuel = fuel_provider.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.5)
    lower = calculate_arc_cost(
        ArcTravelInput(first, 10.0), cost_rates, scenario, fuel
    ).reported_variable_cost_eur
    upper = calculate_arc_cost(
        ArcTravelInput(first + increment, 10.0), cost_rates, scenario, fuel
    ).reported_variable_cost_eur
    assert upper >= lower
