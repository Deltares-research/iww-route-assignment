from pathlib import Path

import pytest

from iww_route_assignment import (
    Confidence,
    DuplicateFuelObservationError,
    FallbackFuelConsumptionProvider,
    FuelConsumptionObservation,
    FuelCoverageError,
    FuelResolutionMethod,
    FuelResolutionPolicy,
    LoadingCategory,
    TableFuelConsumptionProvider,
    ValidationError,
    VesselProfile,
)
from iww_route_assignment.io import (
    load_fuel_observations_csv,
    load_vessel_references_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_exact_lookup() -> None:
    provider = _provider()
    result = provider.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.HEAVY
    )
    assert result.fuel_rate_l_per_h == 80.0
    assert result.method is FuelResolutionMethod.OBSERVED
    assert result.confidence is Confidence.HIGH
    assert result.source_row_ids == ("general:M1:I",)


def test_loading_interpolation() -> None:
    result = _provider().resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=0.25)
    assert result.fuel_rate_l_per_h == 50.0
    assert result.lower_loading_category is LoadingCategory.LIGHT
    assert result.upper_loading_category is LoadingCategory.MEDIUM
    assert result.loading_interpolation_fraction == 0.5
    assert result.method is FuelResolutionMethod.LOADING_INTERPOLATED


def test_explicit_container_profile_does_not_collide_with_general() -> None:
    provider = _provider()
    general = provider.resolve(
        "M1", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.MEDIUM
    )
    container = provider.resolve(
        "M1", "I", VesselProfile.CONTAINER, loading_category=LoadingCategory.MEDIUM
    )
    assert general.fuel_rate_l_per_h == 60.0
    assert container.fuel_rate_l_per_h == 50.0


def test_bounded_waterway_interpolation_uses_cemt_rank() -> None:
    result = _provider().resolve(
        "M1", "II", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    assert result.fuel_rate_l_per_h == pytest.approx(45.0)
    assert result.waterway_interpolation_fraction == pytest.approx(0.25)
    assert result.lower_waterway_cemt_class == "I"
    assert result.upper_waterway_cemt_class == "Va"
    assert result.method is FuelResolutionMethod.WATERWAY_INTERPOLATED
    assert result.confidence is Confidence.MEDIUM


def test_strict_policy_rejects_out_of_bounds_waterway() -> None:
    provider = _provider(policy=FuelResolutionPolicy.STRICT)
    with pytest.raises(FuelCoverageError, match="bounded waterway coverage"):
        provider.resolve("M1", "0", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT)


def test_audited_policy_uses_nearest_boundary_without_extrapolation() -> None:
    result = _provider().resolve(
        "M1", "0", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    assert result.fuel_rate_l_per_h == 40.0
    assert result.selected_waterway_cemt_class == "I"
    assert result.method is FuelResolutionMethod.WATERWAY_PROXY
    assert result.confidence is Confidence.LOW


def test_nearest_vessel_proxy_exposes_distance_terms() -> None:
    result = _provider().resolve(
        "M3", "Va", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    assert result.selected_vessel_rws_class == "M2"
    assert result.fuel_rate_l_per_h == 100.0
    assert result.method is FuelResolutionMethod.VESSEL_PROXY
    assert result.proxy_distance is not None
    assert dict(result.proxy_distance_terms)["log_capacity"] > 0
    assert result.proxy_weights_version == "v1"


def test_proxy_selection_is_independent_of_input_row_order() -> None:
    observations = load_fuel_observations_csv(FIXTURES / "fuel_observations.csv")
    references = load_vessel_references_csv(FIXTURES / "vessel_references.csv")
    forward = TableFuelConsumptionProvider(observations, references)
    reverse = TableFuelConsumptionProvider(reversed(observations), reversed(references))
    request = ("M3", "Va", VesselProfile.GENERAL)
    assert forward.resolve(*request, loading_category=LoadingCategory.LIGHT) == reverse.resolve(
        *request, loading_category=LoadingCategory.LIGHT
    )


def test_profile_is_preserved_during_vessel_proxy() -> None:
    result = _provider().resolve(
        "M2", "I", VesselProfile.CONTAINER, loading_category=LoadingCategory.LIGHT
    )
    assert result.selected_vessel_rws_class == "M1"
    assert result.selected_profile is VesselProfile.CONTAINER
    assert result.fuel_rate_l_per_h == 30.0


def test_unknown_vessel_without_reference_fails_actionably() -> None:
    with pytest.raises(FuelCoverageError, match="no vessel reference"):
        _provider().resolve(
            "UNKNOWN", "I", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
        )


def test_duplicate_profile_key_is_rejected() -> None:
    observation = load_fuel_observations_csv(FIXTURES / "fuel_observations.csv")[0]
    duplicate = FuelConsumptionObservation(
        source_row_id="other-row",
        vessel_rws_class=observation.vessel_rws_class,
        waterway_cemt_class=observation.waterway_cemt_class,
        vessel_profile=observation.vessel_profile,
        diesel_light_l_per_h=1.0,
        diesel_medium_l_per_h=2.0,
        diesel_heavy_l_per_h=3.0,
        source_dataset="other",
    )
    with pytest.raises(DuplicateFuelObservationError, match="Duplicate fuel observation"):
        TableFuelConsumptionProvider((observation, duplicate))


@pytest.mark.parametrize(
    ("ratio", "category"),
    [(None, None), (0.5, LoadingCategory.MEDIUM)],
)
def test_exactly_one_loading_selector_is_required(
    ratio: float | None, category: LoadingCategory | None
) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _provider().resolve(
            "M1",
            "I",
            VesselProfile.GENERAL,
            loading_ratio=ratio,
            loading_category=category,
        )


def test_loading_ratio_is_clamped_and_recorded() -> None:
    result = _provider().resolve("M1", "I", VesselProfile.GENERAL, loading_ratio=1.2)
    assert result.raw_loading_ratio == 1.2
    assert result.applied_loading_ratio == 1.0
    assert result.fuel_rate_l_per_h == 80.0
    assert "clamped" in result.notes[0]


def test_fallback_provider_is_labelled() -> None:
    primary = _provider(policy=FuelResolutionPolicy.STRICT)
    fallback = _provider(policy=FuelResolutionPolicy.AUDITED_PROXY)
    provider = FallbackFuelConsumptionProvider(primary, fallback)
    result = provider.resolve(
        "M1", "0", VesselProfile.GENERAL, loading_category=LoadingCategory.LIGHT
    )
    assert result.method is FuelResolutionMethod.STREAM_FALLBACK
    assert result.confidence is Confidence.FALLBACK


def _provider(
    *, policy: FuelResolutionPolicy = FuelResolutionPolicy.AUDITED_PROXY
) -> TableFuelConsumptionProvider:
    return TableFuelConsumptionProvider(
        load_fuel_observations_csv(FIXTURES / "fuel_observations.csv"),
        load_vessel_references_csv(FIXTURES / "vessel_references.csv"),
        policy=policy,
    )
