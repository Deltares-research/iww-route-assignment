"""Auditable fuel-consumption lookup, interpolation and proxy selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from math import isclose, isfinite, log
from typing import Protocol

from ..domain.classifications import (
    CEMT_ORDER,
    cemt_rank,
    normalise_cemt_class,
    normalise_rws_class,
)
from ..domain.enums import (
    Confidence,
    FuelResolutionMethod,
    FuelResolutionPolicy,
    LoadingCategory,
)
from ..domain.models import (
    FuelConsumptionObservation,
    FuelProxyWeights,
    LoadingAnchors,
    ResolvedFuelRate,
    VesselReference,
)
from ..exceptions import DuplicateFuelObservationError, FuelCoverageError, ValidationError


class FuelConsumptionProvider(Protocol):
    """Resolve one auditable fuel rate without depending on a file format."""

    def resolve(
        self,
        vessel_rws_class: str,
        waterway_cemt_class: str,
        *,
        loading_ratio: float | None = None,
        loading_category: LoadingCategory | None = None,
    ) -> ResolvedFuelRate:
        """Resolve fuel use in litres per hour for one request."""


@dataclass(frozen=True, slots=True)
class _LoadingResult:
    rate_l_per_h: float
    raw_ratio: float | None
    applied_ratio: float
    lower_category: LoadingCategory
    upper_category: LoadingCategory
    fraction: float
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _WaterwayResult:
    rate_l_per_h: float
    selected_class: str
    lower_class: str
    upper_class: str
    fraction: float
    observations: tuple[FuelConsumptionObservation, ...]
    loading: _LoadingResult
    method: FuelResolutionMethod
    confidence: Confidence
    notes: tuple[str, ...]


class TableFuelConsumptionProvider:
    """Resolve unambiguous vessel/waterway observations with configurable coverage policy."""

    def __init__(
        self,
        observations: Iterable[FuelConsumptionObservation],
        vessel_references: Iterable[VesselReference] = (),
        *,
        policy: FuelResolutionPolicy = FuelResolutionPolicy.AUDITED_PROXY,
        loading_anchors: LoadingAnchors | None = None,
        proxy_weights: FuelProxyWeights | None = None,
    ) -> None:
        self._policy = FuelResolutionPolicy(policy)
        self._loading_anchors = loading_anchors or LoadingAnchors()
        self._proxy_weights = proxy_weights or FuelProxyWeights()
        self._observations: dict[tuple[str, str], FuelConsumptionObservation] = {}
        self._by_vessel: dict[str, list[FuelConsumptionObservation]] = {}

        for observation in observations:
            key = (
                observation.vessel_rws_class,
                observation.waterway_cemt_class,
            )
            previous = self._observations.get(key)
            if previous is not None:
                raise DuplicateFuelObservationError(
                    "Duplicate fuel observation for "
                    f"vessel={key[0]!r}, waterway={key[1]!r}: "
                    f"{previous.source_row_id!r} and {observation.source_row_id!r}"
                )
            self._observations[key] = observation
            self._by_vessel.setdefault(key[0], []).append(observation)

        if not self._observations:
            raise ValidationError("At least one fuel observation is required")

        for rows in self._by_vessel.values():
            rows.sort(key=lambda row: (cemt_rank(row.waterway_cemt_class), row.source_row_id))

        references = list(vessel_references)
        self._references = {reference.vessel_rws_class: reference for reference in references}
        if len(self._references) != len(references):
            raise ValidationError("vessel_references contains duplicate vessel_rws_class values")

    def resolve(
        self,
        vessel_rws_class: str,
        waterway_cemt_class: str,
        *,
        loading_ratio: float | None = None,
        loading_category: LoadingCategory | None = None,
    ) -> ResolvedFuelRate:
        """Resolve an observed, interpolated or explicitly proxied rate."""
        requested_vessel = normalise_rws_class(vessel_rws_class)
        requested_waterway = normalise_cemt_class(waterway_cemt_class)
        self._validate_loading_request(loading_ratio, loading_category)

        selected_vessel = requested_vessel
        proxy_distance: float | None = None
        proxy_terms: tuple[tuple[str, float], ...] = ()
        rows = self._by_vessel.get(requested_vessel)

        if not rows:
            if self._policy is FuelResolutionPolicy.STRICT:
                raise self._coverage_error(requested_vessel, requested_waterway, "vessel")
            selected_vessel, proxy_distance, proxy_terms = self._nearest_vessel(requested_vessel)
            rows = self._by_vessel[selected_vessel]

        waterway = self._resolve_waterway(
            rows,
            requested_waterway,
            loading_ratio=loading_ratio,
            loading_category=loading_category,
        )

        method = waterway.method
        confidence = waterway.confidence
        notes = list(waterway.notes)
        weights_version: str | None = None
        if selected_vessel != requested_vessel:
            method = FuelResolutionMethod.VESSEL_PROXY
            confidence = Confidence.LOW
            weights_version = self._proxy_weights.version
            notes.append(
                f"Fuel vessel proxy {requested_vessel} -> {selected_vessel} selected with "
                f"weights {self._proxy_weights.version}."
            )

        return ResolvedFuelRate(
            fuel_rate_l_per_h=waterway.rate_l_per_h,
            requested_vessel_rws_class=requested_vessel,
            selected_vessel_rws_class=selected_vessel,
            requested_waterway_cemt_class=requested_waterway,
            selected_waterway_cemt_class=waterway.selected_class,
            raw_loading_ratio=waterway.loading.raw_ratio,
            applied_loading_ratio=waterway.loading.applied_ratio,
            lower_loading_category=waterway.loading.lower_category,
            upper_loading_category=waterway.loading.upper_category,
            loading_interpolation_fraction=waterway.loading.fraction,
            lower_waterway_cemt_class=waterway.lower_class,
            upper_waterway_cemt_class=waterway.upper_class,
            waterway_interpolation_fraction=waterway.fraction,
            method=method,
            confidence=confidence,
            source_row_ids=tuple(row.source_row_id for row in waterway.observations),
            source_datasets=tuple(sorted({row.source_dataset for row in waterway.observations})),
            proxy_distance=proxy_distance,
            proxy_distance_terms=proxy_terms,
            proxy_weights_version=weights_version,
            notes=tuple((*waterway.loading.notes, *notes)),
        )

    @staticmethod
    def _validate_loading_request(
        loading_ratio: float | None, loading_category: LoadingCategory | None
    ) -> None:
        if (loading_ratio is None) == (loading_category is None):
            raise ValidationError(
                "Supply exactly one of loading_ratio or loading_category for fuel resolution"
            )
        if loading_ratio is not None and not isfinite(loading_ratio):
            raise ValidationError("loading_ratio must be finite")
        if loading_category is not None:
            try:
                LoadingCategory(loading_category)
            except ValueError as error:
                raise ValidationError(
                    f"Unsupported loading_category {loading_category!r}"
                ) from error

    def _resolve_waterway(
        self,
        rows: list[FuelConsumptionObservation],
        requested_waterway: str,
        *,
        loading_ratio: float | None,
        loading_category: LoadingCategory | None,
    ) -> _WaterwayResult:
        target_rank = cemt_rank(requested_waterway)
        exact = next((row for row in rows if row.waterway_cemt_class == requested_waterway), None)
        if exact is not None:
            loading = self._resolve_loading(exact, loading_ratio, loading_category)
            method = (
                FuelResolutionMethod.LOADING_INTERPOLATED
                if loading.lower_category != loading.upper_category
                else FuelResolutionMethod.OBSERVED
            )
            return _WaterwayResult(
                rate_l_per_h=loading.rate_l_per_h,
                selected_class=requested_waterway,
                lower_class=requested_waterway,
                upper_class=requested_waterway,
                fraction=0.0,
                observations=(exact,),
                loading=loading,
                method=method,
                confidence=Confidence.HIGH,
                notes=(),
            )

        lower_rows = [row for row in rows if cemt_rank(row.waterway_cemt_class) < target_rank]
        upper_rows = [row for row in rows if cemt_rank(row.waterway_cemt_class) > target_rank]
        if lower_rows and upper_rows:
            lower = max(lower_rows, key=lambda row: cemt_rank(row.waterway_cemt_class))
            upper = min(upper_rows, key=lambda row: cemt_rank(row.waterway_cemt_class))
            lower_rank = cemt_rank(lower.waterway_cemt_class)
            upper_rank = cemt_rank(upper.waterway_cemt_class)
            fraction = (target_rank - lower_rank) / (upper_rank - lower_rank)
            lower_loading = self._resolve_loading(lower, loading_ratio, loading_category)
            upper_loading = self._resolve_loading(upper, loading_ratio, loading_category)
            rate = lower_loading.rate_l_per_h + fraction * (
                upper_loading.rate_l_per_h - lower_loading.rate_l_per_h
            )
            return _WaterwayResult(
                rate_l_per_h=rate,
                selected_class=requested_waterway,
                lower_class=lower.waterway_cemt_class,
                upper_class=upper.waterway_cemt_class,
                fraction=fraction,
                observations=(lower, upper),
                loading=lower_loading,
                method=FuelResolutionMethod.WATERWAY_INTERPOLATED,
                confidence=Confidence.MEDIUM,
                notes=(),
            )

        if self._policy is FuelResolutionPolicy.STRICT:
            row = rows[0]
            raise self._coverage_error(
                row.vessel_rws_class,
                requested_waterway,
                "bounded waterway coverage",
            )

        nearest = min(
            rows,
            key=lambda row: (
                abs(cemt_rank(row.waterway_cemt_class) - target_rank),
                cemt_rank(row.waterway_cemt_class),
                row.source_row_id,
            ),
        )
        loading = self._resolve_loading(nearest, loading_ratio, loading_category)
        return _WaterwayResult(
            rate_l_per_h=loading.rate_l_per_h,
            selected_class=nearest.waterway_cemt_class,
            lower_class=nearest.waterway_cemt_class,
            upper_class=nearest.waterway_cemt_class,
            fraction=0.0,
            observations=(nearest,),
            loading=loading,
            method=FuelResolutionMethod.WATERWAY_PROXY,
            confidence=Confidence.LOW,
            notes=(
                f"Nearest observed waterway {nearest.waterway_cemt_class} used for "
                f"requested class {requested_waterway}; no numeric extrapolation applied.",
            ),
        )

    def _resolve_loading(
        self,
        observation: FuelConsumptionObservation,
        loading_ratio: float | None,
        loading_category: LoadingCategory | None,
    ) -> _LoadingResult:
        if loading_category is not None:
            category = LoadingCategory(loading_category)
            return _LoadingResult(
                rate_l_per_h=observation.rate_for(category),
                raw_ratio=None,
                applied_ratio=self._loading_anchors.value_for(category),
                lower_category=category,
                upper_category=category,
                fraction=0.0,
                notes=(),
            )

        assert loading_ratio is not None
        applied = min(1.0, max(0.0, loading_ratio))
        notes: tuple[str, ...] = ()
        if not isclose(applied, loading_ratio):
            notes = (f"loading_ratio {loading_ratio:g} clamped to {applied:g}.",)

        anchors = (
            (LoadingCategory.LIGHT, self._loading_anchors.light),
            (LoadingCategory.MEDIUM, self._loading_anchors.medium),
            (LoadingCategory.HEAVY, self._loading_anchors.heavy),
        )
        for category, anchor in anchors:
            if isclose(applied, anchor):
                return _LoadingResult(
                    rate_l_per_h=observation.rate_for(category),
                    raw_ratio=loading_ratio,
                    applied_ratio=applied,
                    lower_category=category,
                    upper_category=category,
                    fraction=0.0,
                    notes=notes,
                )

        if applied < self._loading_anchors.medium:
            lower_category, lower_anchor = anchors[0]
            upper_category, upper_anchor = anchors[1]
        else:
            lower_category, lower_anchor = anchors[1]
            upper_category, upper_anchor = anchors[2]
        fraction = (applied - lower_anchor) / (upper_anchor - lower_anchor)
        lower_rate = observation.rate_for(lower_category)
        upper_rate = observation.rate_for(upper_category)
        return _LoadingResult(
            rate_l_per_h=lower_rate + fraction * (upper_rate - lower_rate),
            raw_ratio=loading_ratio,
            applied_ratio=applied,
            lower_category=lower_category,
            upper_category=upper_category,
            fraction=fraction,
            notes=notes,
        )

    def _nearest_vessel(
        self, requested_vessel: str
    ) -> tuple[str, float, tuple[tuple[str, float], ...]]:
        target = self._references.get(requested_vessel)
        if target is None:
            raise FuelCoverageError(
                f"Cannot proxy vessel {requested_vessel!r}: "
                "no vessel reference attributes available"
            )

        available_classes = sorted(
            vessel for vessel in self._by_vessel if vessel in self._references
        )
        if not available_classes:
            raise FuelCoverageError(
                f"Cannot proxy vessel {requested_vessel!r}: no "
                "fuel observations with vessel reference attributes"
            )

        same_family = [
            vessel
            for vessel in available_classes
            if self._references[vessel].family == target.family
        ]
        candidates = same_family or available_classes
        ranked: list[tuple[float, str, tuple[tuple[str, float], ...]]] = []
        for vessel in candidates:
            candidate = self._references[vessel]
            terms = self._vessel_distance_terms(target, candidate)
            ranked.append((sum(value for _, value in terms), vessel, terms))
        score, vessel, terms = min(ranked, key=lambda item: (item[0], item[1]))
        return vessel, score, terms

    def _vessel_distance_terms(
        self, target: VesselReference, candidate: VesselReference
    ) -> tuple[tuple[str, float], ...]:
        weights = self._proxy_weights
        denominator = len(CEMT_ORDER) - 1
        return (
            (
                "family_mismatch",
                weights.family_mismatch * float(target.family != candidate.family),
            ),
            (
                "cemt_rank",
                weights.cemt_rank
                * abs(cemt_rank(target.vessel_cemt_class) - cemt_rank(candidate.vessel_cemt_class))
                / denominator,
            ),
            (
                "log_capacity",
                weights.log_capacity * abs(log(candidate.capacity_t / target.capacity_t)),
            ),
            ("beam", weights.beam * abs(candidate.beam_m - target.beam_m) / target.beam_m),
            (
                "length",
                weights.length * abs(candidate.length_m - target.length_m) / target.length_m,
            ),
            (
                "draught_loaded",
                weights.draught_loaded
                * abs(candidate.draught_loaded_m - target.draught_loaded_m)
                / target.draught_loaded_m,
            ),
            (
                "draught_empty",
                weights.draught_empty
                * abs(candidate.draught_empty_m - target.draught_empty_m)
                / target.draught_empty_m,
            ),
        )

    @staticmethod
    def _coverage_error(vessel: str, waterway: str, stage: str) -> FuelCoverageError:
        return FuelCoverageError(
            f"No fuel coverage for vessel={vessel!r}, waterway={waterway!r}, at {stage}"
        )


class FallbackFuelConsumptionProvider:
    """Use a compatibility provider only after the primary reports no coverage."""

    def __init__(
        self,
        primary: FuelConsumptionProvider,
        fallback: FuelConsumptionProvider,
        *,
        fallback_name: str = "STREAM compatibility provider",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_name = fallback_name

    def resolve(
        self,
        vessel_rws_class: str,
        waterway_cemt_class: str,
        *,
        loading_ratio: float | None = None,
        loading_category: LoadingCategory | None = None,
    ) -> ResolvedFuelRate:
        """Resolve from primary, then label an explicit compatibility fallback."""
        try:
            return self._primary.resolve(
                vessel_rws_class,
                waterway_cemt_class,
                loading_ratio=loading_ratio,
                loading_category=loading_category,
            )
        except FuelCoverageError as primary_error:
            fallback = self._fallback.resolve(
                vessel_rws_class,
                waterway_cemt_class,
                loading_ratio=loading_ratio,
                loading_category=loading_category,
            )
            return replace(
                fallback,
                method=FuelResolutionMethod.STREAM_FALLBACK,
                confidence=Confidence.FALLBACK,
                notes=(*fallback.notes, f"Used {self._fallback_name}: {primary_error}"),
            )
