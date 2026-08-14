from dataclasses import replace

import pytest

from iww_route_assignment import (
    ArcTravelInput,
    FuelConsumptionObservation,
    FuelProxyWeights,
    LoadingAnchors,
    ScenarioCostParameters,
    TripCostInput,
    ValidationError,
    VesselProfile,
    VesselReference,
    profile_for_appearance,
)
from iww_route_assignment.domain import normalise_cemt_class, normalise_rws_class


def test_class_aliases_are_normalised() -> None:
    assert normalise_rws_class(" BII-6B ") == "BII-6b"
    assert normalise_cemt_class("VIB") == "VIb"


def test_appearance_selects_profile() -> None:
    assert profile_for_appearance("Container") is VesselProfile.CONTAINER
    assert profile_for_appearance("Dry bulk") is VesselProfile.GENERAL


@pytest.mark.parametrize("field", ["distance_km", "lock_waiting_time_h"])
def test_arc_rejects_negative_physical_values(field: str) -> None:
    values = {"distance_km": 1.0, "realised_speed_km_per_h": 10.0, field: -1.0}
    with pytest.raises(ValidationError, match="non-negative"):
        ArcTravelInput(**values)


@pytest.mark.parametrize("speed", [0.0, -1.0, float("nan"), float("inf")])
def test_arc_requires_positive_finite_speed(speed: float) -> None:
    with pytest.raises(ValidationError, match="greater than zero"):
        ArcTravelInput(distance_km=1.0, realised_speed_km_per_h=speed)


def test_empty_trip_rejects_cargo() -> None:
    with pytest.raises(ValidationError, match="empty trip"):
        TripCostInput(cargo_t=1.0, is_loaded=False)


def test_scenario_rejects_idle_factor_above_one() -> None:
    with pytest.raises(ValidationError, match="must not exceed 1"):
        ScenarioCostParameters(1.0, 3.0, 2.0, idle_fuel_factor=1.1)


def test_loading_anchors_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="light < medium < heavy"):
        LoadingAnchors(light=0.0, medium=1.0, heavy=0.5)


def test_proxy_weights_require_a_positive_component() -> None:
    weights = FuelProxyWeights()
    values = {
        field: 0.0
        for field in (
            "family_mismatch",
            "cemt_rank",
            "log_capacity",
            "beam",
            "length",
            "draught_loaded",
            "draught_empty",
        )
    }
    with pytest.raises(ValidationError, match="At least one"):
        replace(weights, **values)


def test_fuel_observation_normalises_keys() -> None:
    observation = FuelConsumptionObservation(
        source_row_id="row",
        vessel_rws_class="BII-6B",
        waterway_cemt_class="VIC",
        vessel_profile=VesselProfile.GENERAL,
        diesel_light_l_per_h=1.0,
        diesel_medium_l_per_h=2.0,
        diesel_heavy_l_per_h=3.0,
        source_dataset="test",
    )
    assert observation.vessel_rws_class == "BII-6b"
    assert observation.waterway_cemt_class == "VIc"


def test_vessel_reference_rejects_missing_physical_values() -> None:
    with pytest.raises(ValidationError, match="capacity_t"):
        VesselReference("M1", "I", 0.0, 5.0, 40.0, 2.0, 1.0)
