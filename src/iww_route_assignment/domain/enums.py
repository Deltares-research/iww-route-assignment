"""Stable enumerations used by the cost module."""

from enum import StrEnum


class VesselProfile(StrEnum):
    """Source profile retained from the fuel observations."""

    GENERAL = "general"
    CONTAINER = "container"


class LoadingCategory(StrEnum):
    """Observed ship-loading categories in the fuel source."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


class FuelResolutionPolicy(StrEnum):
    """Allowed fallback depth for fuel resolution."""

    STRICT = "strict"
    AUDITED_PROXY = "audited_proxy"


class FuelResolutionMethod(StrEnum):
    """Most consequential derivation used for a resolved fuel rate."""

    OBSERVED = "observed"
    LOADING_INTERPOLATED = "loading_interpolated"
    WATERWAY_INTERPOLATED = "waterway_interpolated"
    WATERWAY_PROXY = "waterway_proxy"
    VESSEL_PROXY = "vessel_proxy"
    STREAM_FALLBACK = "stream_fallback"


class Confidence(StrEnum):
    """Initial qualitative confidence assigned to a fuel resolution."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    FALLBACK = "fallback"


class RouteCostMetric(StrEnum):
    """Quantity minimised when choosing a route for a vessel."""

    TRAVEL_TIME = "travel_time"
    TRAVEL_COST_EUR = "travel_cost_eur"


class TripRejectionReason(StrEnum):
    """Reason a source trip record could not become an arc-cost request."""

    UNKNOWN_VESSEL_TYPE = "unknown_vessel_type"
    MISSING_ROUTE_PROFILE = "missing_route_profile"
    MISSING_CARGO_WEIGHT = "missing_cargo_weight"
    NON_POSITIVE_CAPACITY = "non_positive_capacity"
    UNKNOWN_APPEARANCE = "unknown_appearance"
    INVALID_REQUEST = "invalid_request"
