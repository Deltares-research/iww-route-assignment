"""Pure derivation of arc-cost requests from observed trip records.

This module holds no file, database or routing dependency. Adapters in
``iww_route_assignment.io`` read a trip register such as IVS, and this module
derives the vessel class, cargo appearance, loading ratio and route attributes
that the cost calculators require. Every derived field carries a source label,
and every excluded record carries a reason code, so a case run can report
coverage without resolving anything by input order.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .domain.enums import TripRejectionReason
from .domain.models import (
    AppearanceRule,
    ArcCostRequest,
    RouteProfile,
    TripObservation,
    TripRejection,
)
from .exceptions import ValidationError

CONTAINER_APPEARANCE = "container"
"""Appearance label used when a trip carries containers."""

CONTAINER_APPEARANCE_SOURCE = "observed:containers_teu"
"""Source label used when the container count decides the appearance."""

EMPTY_TRIP_APPEARANCE = "overig"
"""Appearance used for a trip that is recorded as empty and carries no cargo code.

An empty vessel has no cargo, so no cargo class can be observed. The residual
class supplies the cost rates for the voyage. The rule is an assumption and is
reported as one.
"""

EMPTY_TRIP_APPEARANCE_ASSUMPTION = "assumption:empty-trip-appearance-v1"
"""Source label for the empty-trip appearance rule."""

NSTR_APPEARANCE_ASSUMPTION = "assumption:nstr-chapter-appearance-v1"
"""Source label for the built-in NSTR chapter mapping.

The mapping is an assumption, not an extract from a BIVAS reference table. It
follows the ten NSTR chapters and assigns liquid chapters to wet bulk, bulk
solids to dry bulk and finished goods to the residual class. Replace it with an
authoritative table when one becomes available, and report it as an assumption
in the coverage report until then.
"""

ASSUMPTION_SOURCE_PREFIX = "assumption:"
"""Prefix that marks a source label as an assumption rather than a reference."""

DEFAULT_NSTR_APPEARANCE: Mapping[str, AppearanceRule] = {
    chapter: AppearanceRule(appearance=appearance, source=NSTR_APPEARANCE_ASSUMPTION)
    for chapter, appearance in (
        ("0", "droge_bulk"),
        ("1", "droge_bulk"),
        ("2", "droge_bulk"),
        ("3", "natte_bulk"),
        ("4", "droge_bulk"),
        ("5", "overig"),
        ("6", "droge_bulk"),
        ("7", "droge_bulk"),
        ("8", "natte_bulk"),
        ("9", "overig"),
    )
}
"""NSTR chapter to cargo appearance, keyed on the leading chapter digit."""


@dataclass(frozen=True, slots=True)
class TripConversionResult:
    """Accepted arc-cost requests and audited rejections for one preparation run."""

    requests: tuple[ArcCostRequest, ...]
    rejections: tuple[TripRejection, ...]


def build_route_index(profiles: Iterable[RouteProfile]) -> dict[tuple[str, str], RouteProfile]:
    """Index route profiles by origin and destination, rejecting duplicate pairs."""
    index: dict[tuple[str, str], RouteProfile] = {}
    for profile in profiles:
        if profile.key in index:
            raise ValidationError(
                "Duplicate route profile for origin_locode="
                f"{profile.origin_locode!r}, destination_locode={profile.destination_locode!r}"
            )
        index[profile.key] = profile
    if not index:
        raise ValidationError("At least one route profile is required")
    return index


def build_vessel_type_index(pairs: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Index RWS vessel classes by numeric vessel-type identifier."""
    index: dict[int, str] = {}
    for vessel_type_id, rws_class in pairs:
        if vessel_type_id in index and index[vessel_type_id] != rws_class:
            raise ValidationError(
                f"Conflicting vessel classes for vessel_type_id={vessel_type_id}: "
                f"{index[vessel_type_id]!r} and {rws_class!r}"
            )
        index[vessel_type_id] = rws_class
    if not index:
        raise ValidationError("At least one vessel-type mapping is required")
    return index


def appearance_for_trip(
    trip: TripObservation, appearance_map: Mapping[str, AppearanceRule]
) -> tuple[str, str]:
    """Return the cargo appearance and its source label for one trip."""
    return appearance_for_cargo(trip.cargo_code, trip.cargo_t, trip.containers_teu, appearance_map)


def appearance_for_cargo(
    cargo_code: str,
    cargo_t: float | None,
    containers_teu: float,
    appearance_map: Mapping[str, AppearanceRule],
) -> tuple[str, str]:
    """Return the cargo appearance and its source label for a cargo description."""
    if containers_teu > 0:
        return CONTAINER_APPEARANCE, CONTAINER_APPEARANCE_SOURCE
    chapter = cargo_code.strip()
    if not chapter:
        if cargo_t == 0.0:
            return EMPTY_TRIP_APPEARANCE, EMPTY_TRIP_APPEARANCE_ASSUMPTION
        raise ValidationError("cargo_code is empty and the trip is not recorded as empty")
    key = chapter[0]
    rule = appearance_map.get(key)
    if rule is None:
        raise ValidationError(f"No appearance mapping for cargo_code {cargo_code!r}")
    return rule.appearance, f"{rule.source}:chapter={key}"


def loading_ratio_for_trip(trip: TripObservation) -> tuple[float, str, tuple[str, ...]]:
    """Return the loading ratio, its source label and any clipping note."""
    if trip.cargo_t is None:
        raise ValidationError("cargo_t is missing and the trip is not recorded as empty")
    if trip.capacity_t <= 0:
        raise ValidationError(f"capacity_t must be positive; received {trip.capacity_t!r}")
    raw = trip.cargo_t / trip.capacity_t
    if raw > 1:
        return 1.0, "derived:cargo_t/capacity_t", (f"loading_ratio_clipped_from_{raw:.4f}",)
    return raw, "derived:cargo_t/capacity_t", ()


def trips_to_arc_requests(
    trips: Iterable[TripObservation],
    *,
    routes: Mapping[tuple[str, str], RouteProfile],
    vessel_types: Mapping[int, str],
    appearance_map: Mapping[str, AppearanceRule] | None = None,
) -> TripConversionResult:
    """Derive arc-cost requests from trip records, auditing every exclusion.

    Records are never matched by position. A record without a vessel-class
    mapping, a route profile or a usable capacity is rejected with a reason code
    instead of being repaired with a default.
    """
    mapping = DEFAULT_NSTR_APPEARANCE if appearance_map is None else appearance_map
    requests: list[ArcCostRequest] = []
    rejections: list[TripRejection] = []
    seen: set[str] = set()
    for trip in trips:
        if trip.source_row_id in seen:
            raise ValidationError(f"Duplicate source_row_id {trip.source_row_id!r}")
        seen.add(trip.source_row_id)
        outcome = _convert_trip(trip, routes, vessel_types, mapping)
        if isinstance(outcome, TripRejection):
            rejections.append(outcome)
        else:
            requests.append(outcome)
    return TripConversionResult(requests=tuple(requests), rejections=tuple(rejections))


def summarise_trip_conversion(result: TripConversionResult) -> dict[str, Any]:
    """Summarise coverage, rejection reasons and applied assumptions."""
    total = len(result.requests) + len(result.rejections)
    reason_counts = Counter(rejection.reason.value for rejection in result.rejections)
    appearance_counts = Counter(request.appearance for request in result.requests)
    source_counts = Counter(request.appearance_source for request in result.requests)
    assumed = sum(
        request.appearance_source.startswith(ASSUMPTION_SOURCE_PREFIX)
        for request in result.requests
    )
    clipped = sum(
        any(note.startswith("loading_ratio_clipped") for note in request.notes)
        for request in result.requests
    )
    return {
        "source_rows": total,
        "accepted_rows": len(result.requests),
        "rejected_rows": len(result.rejections),
        "accepted_share": len(result.requests) / total if total else 0.0,
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "appearance_counts": dict(sorted(appearance_counts.items())),
        "appearance_source_counts": dict(sorted(source_counts.items())),
        "assumed_appearance_rows": assumed,
        "assumed_appearance_share": assumed / len(result.requests) if result.requests else 0.0,
        "clipped_loading_ratio_rows": clipped,
    }


def _convert_trip(
    trip: TripObservation,
    routes: Mapping[tuple[str, str], RouteProfile],
    vessel_types: Mapping[int, str],
    appearance_map: Mapping[str, AppearanceRule],
) -> ArcCostRequest | TripRejection:
    rws_class = vessel_types.get(trip.vessel_type_id)
    if rws_class is None:
        return TripRejection(
            source_row_id=trip.source_row_id,
            reason=TripRejectionReason.UNKNOWN_VESSEL_TYPE,
            detail=f"vessel_type_id={trip.vessel_type_id} is not in the vessel-type reference",
        )
    route = routes.get((trip.origin_locode, trip.destination_locode))
    if route is None:
        return TripRejection(
            source_row_id=trip.source_row_id,
            reason=TripRejectionReason.MISSING_ROUTE_PROFILE,
            detail=(
                f"no route profile for {trip.origin_locode}->{trip.destination_locode}; "
                "add the pair to the route-profile table"
            ),
        )
    try:
        appearance, appearance_source = appearance_for_trip(trip, appearance_map)
    except ValidationError as error:
        return TripRejection(
            source_row_id=trip.source_row_id,
            reason=TripRejectionReason.UNKNOWN_APPEARANCE,
            detail=str(error),
        )
    try:
        loading_ratio, loading_source, notes = loading_ratio_for_trip(trip)
    except ValidationError as error:
        reason = (
            TripRejectionReason.MISSING_CARGO_WEIGHT
            if trip.cargo_t is None
            else TripRejectionReason.NON_POSITIVE_CAPACITY
        )
        return TripRejection(
            source_row_id=trip.source_row_id,
            reason=reason,
            detail=str(error),
        )
    cargo_t = trip.cargo_t if trip.cargo_t is not None else 0.0
    try:
        return ArcCostRequest(
            case_row_id=trip.source_row_id,
            arc_id=f"{route.origin_locode}->{route.destination_locode}",
            appearance=appearance,
            vessel_rws_class=rws_class,
            waterway_cemt_class=route.waterway_cemt_class,
            loading_ratio=loading_ratio,
            distance_km=route.distance_km,
            realised_speed_km_per_h=route.realised_speed_km_per_h,
            cargo_t=cargo_t,
            is_loaded=cargo_t > 0 or trip.containers_teu > 0,
            source_row_id=trip.source_row_id,
            vessel_class_source=f"reference:vessel_type_id={trip.vessel_type_id}",
            appearance_source=appearance_source,
            route_source=route.source,
            loading_ratio_source=loading_source,
            notes=notes,
        )
    except ValidationError as error:
        return TripRejection(
            source_row_id=trip.source_row_id,
            reason=TripRejectionReason.INVALID_REQUEST,
            detail=str(error),
        )
