"""Immutable domain contracts for fuel resolution and monetary costs."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..exceptions import ValidationError
from .classifications import normalise_cemt_class, normalise_rws_class, vessel_family
from .enums import (
    Confidence,
    FuelResolutionMethod,
    LoadingCategory,
    TripRejectionReason,
)


def _finite_non_negative(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValidationError(f"{name} must be finite and non-negative; received {value!r}")


def _finite_positive(name: str, value: float) -> None:
    if not isfinite(value) or value <= 0:
        raise ValidationError(f"{name} must be finite and greater than zero; received {value!r}")


def _required_text(name: str, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValidationError(f"{name} must not be empty")
    return stripped


@dataclass(frozen=True, slots=True)
class CostRates:
    """BIVAS cost rates for one appearance and vessel class."""

    appearance: str
    vessel_rws_class: str
    labour_eur_per_h: float
    labour_waiting_eur_per_h: float
    maintenance_eur_per_h: float
    maintenance_eur_per_km: float
    insurance_eur_per_h: float
    interest_eur_per_percent_h: float
    depreciation_eur_per_h: float
    port_dues_eur_per_h: float
    other_eur_per_h: float
    loading_eur_per_t: float
    unloading_eur_per_t: float
    source: str = "unspecified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "appearance", _required_text("appearance", self.appearance))
        object.__setattr__(self, "vessel_rws_class", normalise_rws_class(self.vessel_rws_class))
        object.__setattr__(self, "source", _required_text("source", self.source))
        for name in (
            "labour_eur_per_h",
            "labour_waiting_eur_per_h",
            "maintenance_eur_per_h",
            "maintenance_eur_per_km",
            "insurance_eur_per_h",
            "interest_eur_per_percent_h",
            "depreciation_eur_per_h",
            "port_dues_eur_per_h",
            "other_eur_per_h",
            "loading_eur_per_t",
            "unloading_eur_per_t",
        ):
            _finite_non_negative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class ScenarioCostParameters:
    """Scenario-level monetary assumptions."""

    fuel_price_eur_per_l: float
    interest_rate_percent: float
    cargo_waiting_time_h: float
    idle_fuel_factor: float = 0.15

    def __post_init__(self) -> None:
        _finite_non_negative("fuel_price_eur_per_l", self.fuel_price_eur_per_l)
        _finite_non_negative("interest_rate_percent", self.interest_rate_percent)
        _finite_non_negative("cargo_waiting_time_h", self.cargo_waiting_time_h)
        _finite_non_negative("idle_fuel_factor", self.idle_fuel_factor)
        if self.idle_fuel_factor > 1:
            raise ValidationError("idle_fuel_factor must not exceed 1")


@dataclass(frozen=True, slots=True)
class FuelConsumptionObservation:
    """Observed fuel use for one vessel and waterway from a selected source."""

    source_row_id: str
    vessel_rws_class: str
    waterway_cemt_class: str
    diesel_light_l_per_h: float
    diesel_medium_l_per_h: float
    diesel_heavy_l_per_h: float
    source_dataset: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_row_id", _required_text("source_row_id", self.source_row_id)
        )
        object.__setattr__(self, "vessel_rws_class", normalise_rws_class(self.vessel_rws_class))
        object.__setattr__(
            self, "waterway_cemt_class", normalise_cemt_class(self.waterway_cemt_class)
        )
        object.__setattr__(
            self, "source_dataset", _required_text("source_dataset", self.source_dataset)
        )
        for name in (
            "diesel_light_l_per_h",
            "diesel_medium_l_per_h",
            "diesel_heavy_l_per_h",
        ):
            _finite_non_negative(name, getattr(self, name))

    def rate_for(self, category: LoadingCategory) -> float:
        """Return the observed rate for a loading category."""
        rates = {
            LoadingCategory.LIGHT: self.diesel_light_l_per_h,
            LoadingCategory.MEDIUM: self.diesel_medium_l_per_h,
            LoadingCategory.HEAVY: self.diesel_heavy_l_per_h,
        }
        return rates[LoadingCategory(category)]


@dataclass(frozen=True, slots=True)
class VesselReference:
    """Vessel attributes used only for deterministic proxy selection."""

    vessel_rws_class: str
    vessel_cemt_class: str
    capacity_t: float
    beam_m: float
    length_m: float
    draught_loaded_m: float
    draught_empty_m: float
    family: str | None = None

    def __post_init__(self) -> None:
        canonical = normalise_rws_class(self.vessel_rws_class)
        object.__setattr__(self, "vessel_rws_class", canonical)
        object.__setattr__(self, "vessel_cemt_class", normalise_cemt_class(self.vessel_cemt_class))
        for name in (
            "capacity_t",
            "beam_m",
            "length_m",
            "draught_loaded_m",
            "draught_empty_m",
        ):
            _finite_positive(name, getattr(self, name))
        family = self.family or vessel_family(canonical)
        object.__setattr__(self, "family", _required_text("family", family))


@dataclass(frozen=True, slots=True)
class LoadingAnchors:
    """Loading-ratio positions for light, medium and heavy observations."""

    light: float = 0.0
    medium: float = 0.5
    heavy: float = 1.0

    def __post_init__(self) -> None:
        for name in ("light", "medium", "heavy"):
            value = getattr(self, name)
            if not isfinite(value):
                raise ValidationError(f"Loading anchor {name} must be finite")
        if not 0 <= self.light < self.medium < self.heavy <= 1:
            raise ValidationError("Loading anchors must satisfy 0 <= light < medium < heavy <= 1")

    def value_for(self, category: LoadingCategory) -> float:
        """Return the configured ratio for a category."""
        return {
            LoadingCategory.LIGHT: self.light,
            LoadingCategory.MEDIUM: self.medium,
            LoadingCategory.HEAVY: self.heavy,
        }[LoadingCategory(category)]


@dataclass(frozen=True, slots=True)
class FuelProxyWeights:
    """Versioned relative weights for nearest-vessel selection."""

    family_mismatch: float = 2.0
    cemt_rank: float = 1.0
    log_capacity: float = 1.0
    beam: float = 0.5
    length: float = 0.5
    draught_loaded: float = 0.5
    draught_empty: float = 0.25
    version: str = "v1"

    def __post_init__(self) -> None:
        for name in (
            "family_mismatch",
            "cemt_rank",
            "log_capacity",
            "beam",
            "length",
            "draught_loaded",
            "draught_empty",
        ):
            _finite_non_negative(name, getattr(self, name))
        if not any(
            getattr(self, name) > 0
            for name in (
                "family_mismatch",
                "cemt_rank",
                "log_capacity",
                "beam",
                "length",
                "draught_loaded",
                "draught_empty",
            )
        ):
            raise ValidationError("At least one fuel proxy weight must be positive")
        object.__setattr__(self, "version", _required_text("version", self.version))


@dataclass(frozen=True, slots=True)
class ResolvedFuelRate:
    """Fuel rate plus a complete derivation record."""

    fuel_rate_l_per_h: float
    requested_vessel_rws_class: str
    selected_vessel_rws_class: str
    requested_waterway_cemt_class: str
    selected_waterway_cemt_class: str
    raw_loading_ratio: float | None
    applied_loading_ratio: float
    lower_loading_category: LoadingCategory
    upper_loading_category: LoadingCategory
    loading_interpolation_fraction: float
    lower_waterway_cemt_class: str
    upper_waterway_cemt_class: str
    waterway_interpolation_fraction: float
    method: FuelResolutionMethod
    confidence: Confidence
    source_row_ids: tuple[str, ...]
    source_datasets: tuple[str, ...]
    proxy_distance: float | None = None
    proxy_distance_terms: tuple[tuple[str, float], ...] = ()
    proxy_weights_version: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _finite_non_negative("fuel_rate_l_per_h", self.fuel_rate_l_per_h)
        if self.raw_loading_ratio is not None and not isfinite(self.raw_loading_ratio):
            raise ValidationError("raw_loading_ratio must be finite when supplied")
        if not 0 <= self.applied_loading_ratio <= 1:
            raise ValidationError("applied_loading_ratio must be between 0 and 1")
        for name in ("loading_interpolation_fraction", "waterway_interpolation_fraction"):
            value = getattr(self, name)
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValidationError(f"{name} must be finite and between 0 and 1")
        if self.proxy_distance is not None:
            _finite_non_negative("proxy_distance", self.proxy_distance)
        if not self.source_row_ids:
            raise ValidationError("Resolved fuel rates require at least one source_row_id")
        if not self.source_datasets:
            raise ValidationError("Resolved fuel rates require at least one source_dataset")


@dataclass(frozen=True, slots=True)
class ArcTravelInput:
    """Time inputs for one directed arc and one trip class."""

    distance_km: float
    realised_speed_km_per_h: float
    lock_service_time_h: float = 0.0
    lock_waiting_time_h: float = 0.0
    bridge_service_time_h: float = 0.0
    real_delay_time_h: float = 0.0
    calibration_delay_time_h: float = 0.0
    relaxation_penalty_time_h: float = 0.0

    def __post_init__(self) -> None:
        _finite_non_negative("distance_km", self.distance_km)
        _finite_positive("realised_speed_km_per_h", self.realised_speed_km_per_h)
        for name in (
            "lock_service_time_h",
            "lock_waiting_time_h",
            "bridge_service_time_h",
            "real_delay_time_h",
            "calibration_delay_time_h",
            "relaxation_penalty_time_h",
        ):
            _finite_non_negative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class TripCostInput:
    """Route-independent trip inputs."""

    cargo_t: float
    is_loaded: bool

    def __post_init__(self) -> None:
        _finite_non_negative("cargo_t", self.cargo_t)
        if not isinstance(self.is_loaded, bool):
            raise ValidationError("is_loaded must be a bool")
        if not self.is_loaded and self.cargo_t > 0:
            raise ValidationError("An empty trip cannot carry positive cargo_t")


@dataclass(frozen=True, slots=True)
class ArcCostBreakdown:
    """Reported and route-choice cost components for one arc."""

    movement_time_h: float
    reported_fixed_time_h: float
    reported_time_h: float
    choice_only_time_h: float
    choice_time_h: float
    distance_cost_eur: float
    labour_cost_eur: float
    maintenance_time_cost_eur: float
    insurance_cost_eur: float
    interest_cost_eur: float
    depreciation_cost_eur: float
    port_dues_cost_eur: float
    other_cost_eur: float
    time_cost_eur: float
    sailing_fuel_l: float
    idle_fuel_l: float
    sailing_fuel_cost_eur: float
    idle_fuel_cost_eur: float
    reported_variable_cost_eur: float
    choice_only_cost_eur: float
    choice_cost_eur: float
    fuel: ResolvedFuelRate


@dataclass(frozen=True, slots=True)
class TripFixedCostBreakdown:
    """Route-independent fixed cost components."""

    loading_cost_eur: float
    unloading_cost_eur: float
    cargo_waiting_cost_eur: float
    total_fixed_cost_eur: float


@dataclass(frozen=True, slots=True)
class TripCostBreakdown:
    """Reported trip total assembled from arc and fixed costs."""

    arc_reported_cost_eur: float
    fixed_cost_eur: float
    reported_total_cost_eur: float
    arc_choice_cost_eur: float
    choice_total_cost_eur: float

    @property
    def total_cost_eur(self) -> float:
        """Generalised trip cost including explicitly supplied choice penalties."""
        return self.choice_total_cost_eur


@dataclass(frozen=True, slots=True)
class TripObservation:
    """One observed origin-destination voyage from a trip register such as IVS."""

    source_row_id: str
    origin_locode: str
    destination_locode: str
    vessel_type_id: int | None
    capacity_t: float
    cargo_t: float | None
    containers_teu: float | None
    cargo_code: str
    source_dataset: str
    loading_code: str = ""
    observed_on: str = ""
    vessel_rws_class: str = ""
    appearance: str = ""
    appearance_source: str = ""
    cargo_unit_source: str = ""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source_row_id", "origin_locode", "destination_locode", "source_dataset"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(self, "origin_locode", self.origin_locode.upper())
        object.__setattr__(self, "destination_locode", self.destination_locode.upper())
        object.__setattr__(self, "cargo_code", self.cargo_code.strip())
        object.__setattr__(self, "loading_code", self.loading_code.strip())
        object.__setattr__(self, "observed_on", self.observed_on.strip())
        if self.vessel_type_id is not None and (
            not isinstance(self.vessel_type_id, int) or isinstance(self.vessel_type_id, bool)
        ):
            raise ValidationError(
                f"vessel_type_id must be an int; received {self.vessel_type_id!r}"
            )
        _finite_non_negative("capacity_t", self.capacity_t)
        if self.containers_teu is not None:
            _finite_non_negative("containers_teu", self.containers_teu)
        if self.cargo_t is not None:
            _finite_non_negative("cargo_t", self.cargo_t)
        if self.vessel_rws_class:
            object.__setattr__(self, "vessel_rws_class", normalise_rws_class(self.vessel_rws_class))
        if self.appearance and not self.appearance_source:
            raise ValidationError("An observed appearance requires appearance_source")


@dataclass(frozen=True, slots=True)
class AppearanceRule:
    """One cargo-code to appearance rule with its own source label."""

    appearance: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "appearance", _required_text("appearance", self.appearance))
        object.__setattr__(self, "source", _required_text("source", self.source))


@dataclass(frozen=True, slots=True)
class ArcCostRequest:
    """One arc-cost request with the provenance of every derived field."""

    case_row_id: str
    arc_id: str
    appearance: str
    vessel_rws_class: str
    waterway_cemt_class: str
    loading_ratio: float
    distance_km: float
    realised_speed_km_per_h: float
    cargo_t: float
    is_loaded: bool
    source_row_id: str
    vessel_class_source: str
    appearance_source: str
    route_source: str
    loading_ratio_source: str
    notes: tuple[str, ...] = ()
    route_id: str = ""
    sequence: int = 0
    route_arc_count: int = 1
    from_node_id: str = ""
    to_node_id: str = ""
    cargo_unit_source: str = ""
    lock_service_time_h: float = 0.0
    lock_waiting_time_h: float = 0.0
    bridge_service_time_h: float = 0.0
    real_delay_time_h: float = 0.0
    calibration_delay_time_h: float = 0.0
    relaxation_penalty_time_h: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "case_row_id",
            "arc_id",
            "appearance",
            "source_row_id",
            "vessel_class_source",
            "appearance_source",
            "route_source",
            "loading_ratio_source",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(self, "vessel_rws_class", normalise_rws_class(self.vessel_rws_class))
        object.__setattr__(
            self, "waterway_cemt_class", normalise_cemt_class(self.waterway_cemt_class)
        )
        if not isfinite(self.loading_ratio) or not 0 <= self.loading_ratio <= 1:
            raise ValidationError(
                f"loading_ratio must be finite and between 0 and 1; received {self.loading_ratio!r}"
            )
        _finite_non_negative("distance_km", self.distance_km)
        _finite_positive("realised_speed_km_per_h", self.realised_speed_km_per_h)
        _finite_non_negative("cargo_t", self.cargo_t)
        if not isinstance(self.is_loaded, bool):
            raise ValidationError("is_loaded must be a bool")
        if not self.is_loaded and self.cargo_t > 0:
            raise ValidationError("An empty trip cannot carry positive cargo_t")
        if (
            type(self.sequence) is not int
            or type(self.route_arc_count) is not int
            or not 0 <= self.sequence < self.route_arc_count
        ):
            raise ValidationError("sequence must be an integer within route_arc_count")
        for name in (
            "lock_service_time_h",
            "lock_waiting_time_h",
            "bridge_service_time_h",
            "real_delay_time_h",
            "calibration_delay_time_h",
            "relaxation_penalty_time_h",
        ):
            _finite_non_negative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class TripRejection:
    """A source trip record excluded from the case, with an auditable reason."""

    source_row_id: str
    reason: TripRejectionReason
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_row_id", _required_text("source_row_id", self.source_row_id)
        )
        try:
            reason = TripRejectionReason(self.reason)
        except ValueError as error:
            raise ValidationError(f"Unsupported rejection reason {self.reason!r}") from error
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "detail", _required_text("detail", self.detail))
