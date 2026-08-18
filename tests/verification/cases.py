"""Hand-calculated verification cases for the cost and fuel formulas.

Each case states the requirement it verifies, the arithmetic performed by hand
and the expected values. The expected values are written out in full so that a
reviewer can repeat the calculation without running the code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from iww_route_assignment import (
    ArcTravelInput,
    CostRates,
    FuelConsumptionObservation,
    LoadingCategory,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    TripCostInput,
    VesselProfile,
    VesselReference,
    calculate_arc_cost,
    calculate_arc_costs,
    calculate_trip_fixed_cost,
    combine_trip_cost,
)

TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class VerificationCase:
    """One verifiable claim with its hand calculation and expected values."""

    case_id: str
    requirement: str
    description: str
    hand_calculation: str
    expected: Mapping[str, Any]
    compute: Callable[[], Mapping[str, Any]]


RATES = CostRates(
    appearance="droge_bulk",
    vessel_rws_class="M1",
    labour_eur_per_h=20.0,
    labour_waiting_eur_per_h=15.0,
    maintenance_eur_per_h=5.0,
    maintenance_eur_per_km=2.0,
    insurance_eur_per_h=3.0,
    interest_eur_per_percent_h=1.0,
    depreciation_eur_per_h=10.0,
    port_dues_eur_per_h=4.0,
    other_eur_per_h=6.0,
    loading_eur_per_t=1.0,
    unloading_eur_per_t=2.0,
    source="verification-case",
)

SCENARIO = ScenarioCostParameters(
    fuel_price_eur_per_l=1.5,
    interest_rate_percent=4.0,
    cargo_waiting_time_h=2.0,
    idle_fuel_factor=0.2,
)

ARC = ArcTravelInput(
    distance_km=60.0,
    realised_speed_km_per_h=12.0,
    lock_service_time_h=0.5,
    lock_waiting_time_h=0.25,
    bridge_service_time_h=0.25,
    real_delay_time_h=0.0,
    calibration_delay_time_h=0.5,
    relaxation_penalty_time_h=0.5,
)

OBSERVATIONS = (
    FuelConsumptionObservation(
        source_row_id="verification:M1:I",
        vessel_rws_class="M1",
        waterway_cemt_class="I",
        vessel_profile=VesselProfile.GENERAL,
        diesel_light_l_per_h=40.0,
        diesel_medium_l_per_h=60.0,
        diesel_heavy_l_per_h=80.0,
        source_dataset="verification-case",
    ),
    FuelConsumptionObservation(
        source_row_id="verification:M1:Va",
        vessel_rws_class="M1",
        waterway_cemt_class="Va",
        vessel_profile=VesselProfile.GENERAL,
        diesel_light_l_per_h=60.0,
        diesel_medium_l_per_h=80.0,
        diesel_heavy_l_per_h=100.0,
        source_dataset="verification-case",
    ),
    FuelConsumptionObservation(
        source_row_id="verification:M1:I:container",
        vessel_rws_class="M1",
        waterway_cemt_class="I",
        vessel_profile=VesselProfile.CONTAINER,
        diesel_light_l_per_h=50.0,
        diesel_medium_l_per_h=70.0,
        diesel_heavy_l_per_h=90.0,
        source_dataset="verification-case",
    ),
)

REFERENCES = (
    VesselReference("M1", "I", 350.0, 5.05, 38.5, 2.2, 1.0),
    VesselReference("M2", "II", 550.0, 6.6, 50.0, 2.5, 1.2),
)

PROVIDER = TableFuelConsumptionProvider(OBSERVATIONS, REFERENCES)


def _arc_cost_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    result = calculate_arc_cost(ARC, RATES, SCENARIO, fuel)
    return {
        "movement_time_h": result.movement_time_h,
        "reported_fixed_time_h": result.reported_fixed_time_h,
        "reported_time_h": result.reported_time_h,
        "choice_time_h": result.choice_time_h,
        "distance_cost_eur": result.distance_cost_eur,
        "time_cost_eur": result.time_cost_eur,
        "sailing_fuel_l": result.sailing_fuel_l,
        "idle_fuel_l": result.idle_fuel_l,
        "sailing_fuel_cost_eur": result.sailing_fuel_cost_eur,
        "idle_fuel_cost_eur": result.idle_fuel_cost_eur,
        "reported_variable_cost_eur": result.reported_variable_cost_eur,
        "choice_only_cost_eur": result.choice_only_cost_eur,
        "choice_cost_eur": result.choice_cost_eur,
    }


def _interest_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    result = calculate_arc_cost(ARC, RATES, SCENARIO, fuel)
    return {"interest_cost_eur": result.interest_cost_eur}


def _fixed_cost_actual() -> dict[str, Any]:
    loaded = calculate_trip_fixed_cost(TripCostInput(500.0, True), RATES, SCENARIO)
    empty = calculate_trip_fixed_cost(TripCostInput(0.0, False), RATES, SCENARIO)
    return {
        "loading_cost_eur": loaded.loading_cost_eur,
        "unloading_cost_eur": loaded.unloading_cost_eur,
        "cargo_waiting_cost_eur": loaded.cargo_waiting_cost_eur,
        "total_fixed_cost_eur": loaded.total_fixed_cost_eur,
        "empty_total_fixed_cost_eur": empty.total_fixed_cost_eur,
    }


def _trip_total_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    arc = calculate_arc_cost(ARC, RATES, SCENARIO, fuel)
    fixed = calculate_trip_fixed_cost(TripCostInput(500.0, True), RATES, SCENARIO)
    trip = combine_trip_cost((arc, arc), fixed)
    return {
        "arc_reported_cost_eur": trip.arc_reported_cost_eur,
        "reported_total_cost_eur": trip.reported_total_cost_eur,
        "choice_total_cost_eur": trip.choice_total_cost_eur,
    }


def _loading_interpolation_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.25)
    return {
        "fuel_rate_l_per_h": fuel.fuel_rate_l_per_h,
        "method": fuel.method.value,
        "confidence": fuel.confidence.value,
    }


def _waterway_interpolation_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve(
        "M1", "III", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    return {
        "fuel_rate_l_per_h": fuel.fuel_rate_l_per_h,
        "method": fuel.method.value,
        "confidence": fuel.confidence.value,
    }


def _profile_separation_actual() -> dict[str, Any]:
    general = PROVIDER.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.HEAVY
    )
    container = PROVIDER.resolve(
        "M1", "I", VesselProfile.CONTAINER, loading_category=LoadingCategory.HEAVY
    )
    return {
        "general_fuel_rate_l_per_h": general.fuel_rate_l_per_h,
        "container_fuel_rate_l_per_h": container.fuel_rate_l_per_h,
    }


def _batch_equivalence_actual() -> dict[str, Any]:
    fuel = PROVIDER.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    scalar = calculate_arc_cost(ARC, RATES, SCENARIO, fuel)
    batch = calculate_arc_costs((ARC, ARC), RATES, SCENARIO, (fuel, fuel))
    return {
        "batch_length": len(batch),
        "maximum_absolute_difference": max(
            abs(item.reported_variable_cost_eur - scalar.reported_variable_cost_eur)
            for item in batch
        ),
    }


CASES: tuple[VerificationCase, ...] = (
    VerificationCase(
        case_id="V-COST-001",
        requirement="Arc time and cost decomposition",
        description="Reported and choice cost for one arc with locks and a bridge.",
        hand_calculation=(
            "movement = 60 / 12 = 5 h; fixed = 0.5 + 0.25 + 0.25 = 1 h; reported = 6 h; "
            "choice only = 0.5 + 0.5 = 1 h; distance cost = 2 x 60 = 120; "
            "time cost = (20 + 5 + 3 + 4 x 1 + 10 + 4 + 6) x 6 = 52 x 6 = 312; "
            "sailing fuel = 60 x 5 = 300 l at 1.5 = 450; "
            "idle fuel = 60 x 0.2 x 1 = 12 l at 1.5 = 18; "
            "reported variable = 120 + 312 + 450 + 18 = 900; "
            "choice only cost = 52 x 1 = 52; choice cost = 952"
        ),
        expected={
            "movement_time_h": 5.0,
            "reported_fixed_time_h": 1.0,
            "reported_time_h": 6.0,
            "choice_time_h": 7.0,
            "distance_cost_eur": 120.0,
            "time_cost_eur": 312.0,
            "sailing_fuel_l": 300.0,
            "idle_fuel_l": 12.0,
            "sailing_fuel_cost_eur": 450.0,
            "idle_fuel_cost_eur": 18.0,
            "reported_variable_cost_eur": 900.0,
            "choice_only_cost_eur": 52.0,
            "choice_cost_eur": 952.0,
        },
        compute=_arc_cost_actual,
    ),
    VerificationCase(
        case_id="V-COST-002",
        requirement="Interest is charged per percentage point",
        description="Interest cost multiplies the rate by the interest percentage, not a fraction.",
        hand_calculation="interest = 4 percentage points x 1.0 EUR per point-hour x 6 h = 24",
        expected={"interest_cost_eur": 24.0},
        compute=_interest_actual,
    ),
    VerificationCase(
        case_id="V-COST-003",
        requirement="Route-independent trip cost",
        description="Loading, unloading and cargo waiting apply once, and only to loaded trips.",
        hand_calculation=(
            "loading = 1 x 500 = 500; unloading = 2 x 500 = 1000; waiting = 15 x 2 = 30; "
            "total = 1530; an empty trip pays nothing"
        ),
        expected={
            "loading_cost_eur": 500.0,
            "unloading_cost_eur": 1000.0,
            "cargo_waiting_cost_eur": 30.0,
            "total_fixed_cost_eur": 1530.0,
            "empty_total_fixed_cost_eur": 0.0,
        },
        compute=_fixed_cost_actual,
    ),
    VerificationCase(
        case_id="V-COST-004",
        requirement="Trip total adds fixed cost once",
        description="Two identical arcs plus one fixed trip cost.",
        hand_calculation=(
            "arc reported = 2 x 900 = 1800; total reported = 1800 + 1530 = 3330; "
            "arc choice = 2 x 952 = 1904; total choice = 1904 + 1530 = 3434"
        ),
        expected={
            "arc_reported_cost_eur": 1800.0,
            "reported_total_cost_eur": 3330.0,
            "choice_total_cost_eur": 3434.0,
        },
        compute=_trip_total_actual,
    ),
    VerificationCase(
        case_id="V-FUEL-001",
        requirement="Loading-ratio interpolation",
        description="A ratio of 0.25 lies halfway between the light and medium anchors.",
        hand_calculation=(
            "fraction = (0.25 - 0) / (0.5 - 0) = 0.5; rate = 40 + 0.5 x (60 - 40) = 50"
        ),
        expected={
            "fuel_rate_l_per_h": 50.0,
            "method": "loading_interpolated",
            "confidence": "high",
        },
        compute=_loading_interpolation_actual,
    ),
    VerificationCase(
        case_id="V-FUEL-002",
        requirement="Waterway-class interpolation",
        description="Class III lies halfway between the observed classes I and Va.",
        hand_calculation=(
            "ranks I = 1, III = 3, Va = 5; fraction = (3 - 1) / (5 - 1) = 0.5; "
            "rate = 60 + 0.5 x (80 - 60) = 70"
        ),
        expected={
            "fuel_rate_l_per_h": 70.0,
            "method": "waterway_interpolated",
            "confidence": "medium",
        },
        compute=_waterway_interpolation_actual,
    ),
    VerificationCase(
        case_id="V-FUEL-003",
        requirement="General and container profiles stay separate",
        description="The same vessel and waterway resolve to different rates per profile.",
        hand_calculation="general heavy = 80; container heavy = 90",
        expected={
            "general_fuel_rate_l_per_h": 80.0,
            "container_fuel_rate_l_per_h": 90.0,
        },
        compute=_profile_separation_actual,
    ),
    VerificationCase(
        case_id="V-NUM-001",
        requirement="Scalar and batch paths agree",
        description="The vectorised batch reproduces the readable scalar result exactly.",
        hand_calculation="difference between batch and scalar reported cost = 0",
        expected={"batch_length": 2, "maximum_absolute_difference": 0.0},
        compute=_batch_equivalence_actual,
    ),
)
