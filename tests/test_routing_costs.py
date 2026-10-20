from dataclasses import replace
from pathlib import Path

import pytest

from iww_route_assignment import (
    ArcTravelInput,
    RouteCostMetric,
    RoutingCostConfig,
    ValidationError,
    calculate_arc_choice_time_h,
    calculate_arc_cost,
)
from iww_route_assignment.io import load_cost_rates_csv

FIXTURES = Path(__file__).parent / "fixtures"

ARC = ArcTravelInput(
    distance_km=20.0,
    realised_speed_km_per_h=10.0,
    lock_service_time_h=0.5,
    lock_waiting_time_h=0.25,
    bridge_service_time_h=0.1,
    real_delay_time_h=0.15,
    calibration_delay_time_h=0.2,
    relaxation_penalty_time_h=0.3,
)


def _config(metric, scenario, fuel_provider) -> RoutingCostConfig:
    dry_bulk = load_cost_rates_csv(FIXTURES / "cost_rates.csv")[0]
    return RoutingCostConfig(
        metric=metric,
        rates=(
            replace(dry_bulk, appearance="droge_bulk"),
            replace(dry_bulk, appearance="overig"),
        ),
        fuel_provider=fuel_provider,
        scenario=scenario,
    )


def test_choice_time_matches_calculator(cost_rates, scenario, fuel_provider) -> None:
    from iww_route_assignment import LoadingCategory, VesselProfile

    fuel = fuel_provider.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    expected = calculate_arc_cost(ARC, cost_rates, scenario, fuel).choice_time_h
    assert calculate_arc_choice_time_h(ARC) == pytest.approx(expected)


def test_time_metric_needs_no_fuel_lookup(scenario, fuel_provider) -> None:
    model = _config(RouteCostMetric.TRAVEL_TIME, scenario, fuel_provider).voyage_model(
        vessel_rws_class="M1", cargo_code="1", avg_cargo_t=100.0, capacity_t=300.0
    )
    assert model.edge_weight(ARC, "not-a-cemt-class") == pytest.approx(3.5)


def test_cost_metric_equals_choice_cost(scenario, fuel_provider) -> None:
    model = _config(RouteCostMetric.TRAVEL_COST_EUR, scenario, fuel_provider).voyage_model(
        vessel_rws_class="M1", cargo_code="1", avg_cargo_t=150.0, capacity_t=300.0
    )
    fuel = model.fuel_for("I")
    expected = calculate_arc_cost(ARC, model.rates, scenario, fuel).choice_cost_eur
    assert model.edge_weight(ARC, "I") == pytest.approx(expected)
    assert model.loading_ratio == 0.5


def test_loaded_trip_pays_fixed_cost_once(scenario, fuel_provider) -> None:
    model = _config(RouteCostMetric.TRAVEL_COST_EUR, scenario, fuel_provider).voyage_model(
        vessel_rws_class="M1", cargo_code="1", avg_cargo_t=100.0, capacity_t=300.0
    )
    pricing = model.price_route((ARC, ARC), ("I", "I"))

    assert pricing.is_loaded
    assert pricing.fixed.loading_cost_eur == 2.0 * 100.0
    assert pricing.trip.fixed_cost_eur == pricing.fixed.total_fixed_cost_eur
    assert pricing.trip.arc_reported_cost_eur == pytest.approx(
        2 * pricing.arcs[0].reported_variable_cost_eur
    )
    assert pricing.trip.choice_total_cost_eur > pricing.trip.reported_total_cost_eur


def test_empty_trip_is_priced_with_light_fuel_and_no_handling_cost(scenario, fuel_provider) -> None:
    config = _config(RouteCostMetric.TRAVEL_COST_EUR, scenario, fuel_provider)
    empty = config.voyage_model(
        vessel_rws_class="M1", cargo_code="", avg_cargo_t=0.0, capacity_t=None
    )
    loaded = config.voyage_model(
        vessel_rws_class="M1", cargo_code="", avg_cargo_t=0.0, capacity_t=300.0
    )

    pricing = empty.price_route((ARC,), ("I",))

    assert not pricing.is_loaded
    assert pricing.loading_ratio == 0.0
    assert pricing.appearance == "overig"
    assert pricing.appearance_source.startswith("assumption:")
    assert pricing.fixed.total_fixed_cost_eur == 0.0
    assert pricing.fuel_l > 0
    assert pricing.trip.reported_total_cost_eur > 0
    assert loaded.price_route((ARC,), ("I",)).trip == pricing.trip


def test_loaded_trip_without_capacity_is_rejected(scenario, fuel_provider) -> None:
    config = _config(RouteCostMetric.TRAVEL_COST_EUR, scenario, fuel_provider)
    with pytest.raises(ValidationError, match="capacity_t"):
        config.voyage_model(
            vessel_rws_class="M1", cargo_code="1", avg_cargo_t=10.0, capacity_t=None
        )


def test_loading_ratio_is_clipped_to_one(scenario, fuel_provider) -> None:
    model = _config(RouteCostMetric.TRAVEL_TIME, scenario, fuel_provider).voyage_model(
        vessel_rws_class="M1", cargo_code="1", avg_cargo_t=400.0, capacity_t=300.0
    )
    assert model.loading_ratio == 1.0


def test_price_route_rejects_length_mismatch(scenario, fuel_provider) -> None:
    model = _config(RouteCostMetric.TRAVEL_TIME, scenario, fuel_provider).voyage_model(
        vessel_rws_class="M1", cargo_code="1", avg_cargo_t=100.0, capacity_t=300.0
    )
    with pytest.raises(ValidationError, match="equal length"):
        model.price_route((ARC,), ())
