"""Run settings for traffic assignment. Every value has a unit suffix or a closed enum."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from ..domain.enums import MissingCostPolicy, RouteCostMetric
from ..domain.navigability import NavigabilityMargins
from ..exceptions import ValidationError

DEFAULT_SEA_WATER_NAMES = frozenset({"noordzee", "north sea", "nordsee", "mer du nord"})


def _non_negative(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValidationError(f"{name} must be finite and non-negative; received {value!r}")


@dataclass(frozen=True, slots=True)
class AssignmentSettings:
    """How voyages are snapped, grouped, routed and costed.

    ``metric`` is what the route search minimises. ``travel_cost_eur`` uses the cost
    function, ``travel_time`` uses time only. Both report the full cost of the chosen route.
    ``routing_ratio_step`` rounds the loading ratio used for the search, so voyages can
    share one search. Reported costs always use the exact loading ratio.
    """

    metric: RouteCostMetric = RouteCostMetric.TRAVEL_COST_EUR
    routing_ratio_step: float | None = 0.05
    points_crs: str = "EPSG:28992"
    origin_column: str = "Eerste_Geom"
    destination_column: str = "Laatste_Geom"
    max_snap_distance_m: float | None = None
    on_missing_cost: MissingCostPolicy = MissingCostPolicy.REJECT
    n_workers: int = 1
    origins_per_search: int = 256

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", RouteCostMetric(self.metric))
        object.__setattr__(self, "on_missing_cost", MissingCostPolicy(self.on_missing_cost))
        step = self.routing_ratio_step
        if step is not None and (not isfinite(step) or not 0 < step <= 1):
            raise ValidationError(f"routing_ratio_step must be in (0, 1]; received {step!r}")
        if self.max_snap_distance_m is not None:
            _non_negative("max_snap_distance_m", self.max_snap_distance_m)
        for name in ("n_workers", "origins_per_search"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValidationError(f"{name} must be a positive integer; received {value!r}")
        for name in ("points_crs", "origin_column", "destination_column"):
            if not getattr(self, name).strip():
                raise ValidationError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class NavigabilityPenalties:
    """Route-choice penalty hours for a physical limit the vessel does not meet.

    One penalty per limit type per arc, set by the tightest limit. A too-wide vessel at a
    structure in a country listed in ``flat_beam_countries`` gets ``beam_h``. Elsewhere it
    gets ``beam_abroad_h_per_m`` for every metre over the limit. ``undetermined_h`` applies
    when a limit exists but the vessel value is unknown.

    A penalty of 0 stops a check from steering the route, but every unmet limit is still
    reported. Draught and air draught default to 0: the check uses the full loaded draught
    for any loaded trip and fixed clearances without water levels, so it would overstate them.
    """

    beam_h: float = 1000.0
    beam_abroad_h_per_m: float = 5.0
    length_h: float = 1000.0
    draught_h: float = 0.0
    air_draught_h: float = 0.0
    undetermined_h: float = 0.0
    flat_beam_countries: frozenset[str] = frozenset({"NL"})
    margins: NavigabilityMargins = field(default_factory=NavigabilityMargins)

    def __post_init__(self) -> None:
        for name in (
            "beam_h",
            "beam_abroad_h_per_m",
            "length_h",
            "draught_h",
            "air_draught_h",
            "undetermined_h",
        ):
            _non_negative(name, getattr(self, name))
        countries = frozenset(code.strip().upper() for code in self.flat_beam_countries)
        object.__setattr__(self, "flat_beam_countries", countries)


@dataclass(frozen=True, slots=True)
class RoutePreferences:
    """Route-choice penalty hours that are not physical limits.

    ``cemt_h`` applies when the vessel CEMT class is above the waterway class. ``sea_h``
    applies on arcs whose water name is in ``sea_water_names``.
    """

    cemt_h: float = 1000.0
    sea_h: float = 100000.0
    sea_water_names: frozenset[str] = DEFAULT_SEA_WATER_NAMES

    def __post_init__(self) -> None:
        _non_negative("cemt_h", self.cemt_h)
        _non_negative("sea_h", self.sea_h)
        names = frozenset(name.strip().casefold() for name in self.sea_water_names)
        object.__setattr__(self, "sea_water_names", names)
