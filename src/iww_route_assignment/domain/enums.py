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
