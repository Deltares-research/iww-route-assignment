"""Stable enumerations used by the cost and navigability modules."""

from enum import StrEnum


class IvsFormat(StrEnum):
    """Explicit source layouts; formats are never guessed from individual rows."""

    WEEKMONITOR = "weekmonitor"
    EXTENDED_2026 = "extended_2026"


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
    MISSING_ARC_SEQUENCE = "missing_arc_sequence"
    MISSING_CARGO_WEIGHT = "missing_cargo_weight"
    NON_POSITIVE_CAPACITY = "non_positive_capacity"
    UNKNOWN_APPEARANCE = "unknown_appearance"
    INVALID_REQUEST = "invalid_request"


class StructureType(StrEnum):
    """Physical element that carries a navigability restriction on an arc."""

    WATERWAY = "waterway"
    BRIDGE = "bridge"
    LOCK = "lock"
    WEIR = "weir"


class RestrictionType(StrEnum):
    """Hard physical or regulatory restriction checked by the navigability evaluator."""

    LENGTH = "length"
    BEAM = "beam"
    DRAUGHT = "draught"
    AIR_DRAUGHT = "air_draught"
    DANGEROUS_GOODS = "dangerous_goods"
    CLOSED = "closed"
    COVERAGE = "coverage"


class NavigabilityStatus(StrEnum):
    """Outcome of a navigability check, ordered from permissive to blocking."""

    NAVIGABLE = "navigable"
    UNDETERMINED = "undetermined"
    NOT_NAVIGABLE = "not_navigable"


class PenaltyType(StrEnum):
    """Reason a route-choice penalty was added to an arc for a vessel state."""

    BEAM = "beam"
    LENGTH = "length"
    DRAUGHT = "draught"
    AIR_DRAUGHT = "air_draught"
    UNDETERMINED = "undetermined"
    CEMT = "cemt"
    SEA = "sea"


class VoyageStatus(StrEnum):
    """Outcome of assigning one voyage to the network."""

    SUCCESS = "success"
    NO_PATH = "no_path"
    SNAP_FAIL = "snap_fail"
    VESSEL_FAIL = "vessel_fail"
    COST_FAIL = "cost_fail"


class MissingCostPolicy(StrEnum):
    """What to do when a voyage has no cost rate or fuel reference."""

    REJECT = "reject"
    FAIL = "fail"


class DuplicateTripRule(StrEnum):
    """How repeated trip identifiers in a source export are handled."""

    FAIL = "fail"
    EXCLUDE = "exclude"
