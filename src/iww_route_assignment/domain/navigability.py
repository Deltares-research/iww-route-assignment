"""Immutable navigability contracts, independent of costs, files and route search."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..exceptions import ValidationError
from .enums import NavigabilityStatus, RestrictionType, StructureType
from .models import VesselReference, _finite_non_negative, _finite_positive, _required_text

STATUS_SEVERITY = {
    NavigabilityStatus.NAVIGABLE: 0,
    NavigabilityStatus.UNDETERMINED: 1,
    NavigabilityStatus.NOT_NAVIGABLE: 2,
}

LIMIT_FIELDS = ("max_length_m", "max_beam_m", "max_draught_m", "max_air_draught_m")


def worst_status(statuses: tuple[NavigabilityStatus, ...]) -> NavigabilityStatus:
    """Return the most restrictive status; an empty set is navigable."""
    return max(statuses, key=STATUS_SEVERITY.__getitem__, default=NavigabilityStatus.NAVIGABLE)


def _optional_positive(name: str, value: float | None) -> None:
    if value is not None:
        _finite_positive(name, value)


def _enum_value[E: (StructureType, RestrictionType, NavigabilityStatus)](
    enum_type: type[E], name: str, value: str
) -> E:
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValidationError(f"{name} must be one of {allowed}; received {value!r}") from error


def _strict_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} must be a boolean; received {value!r}")


@dataclass(frozen=True, slots=True)
class ArcRestriction:
    """One structure or waterway limit on a directed arc. Missing limits do not restrict."""

    arc_id: str
    structure_id: str
    structure_type: StructureType
    source: str
    max_length_m: float | None = None
    max_beam_m: float | None = None
    max_draught_m: float | None = None
    max_air_draught_m: float | None = None
    dangerous_goods_permitted: bool = True
    is_open: bool = True

    def __post_init__(self) -> None:
        for name in ("arc_id", "structure_id", "source"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(
            self,
            "structure_type",
            _enum_value(StructureType, "structure_type", self.structure_type),
        )
        for name in LIMIT_FIELDS:
            _optional_positive(name, getattr(self, name))
        _strict_bool("dangerous_goods_permitted", self.dangerous_goods_permitted)
        _strict_bool("is_open", self.is_open)


@dataclass(frozen=True, slots=True)
class NavigabilityVessel:
    """Physical vessel state for one trip class; draught already reflects loading."""

    vessel_id: str
    length_m: float
    beam_m: float
    draught_m: float
    air_draught_m: float | None = None
    carries_dangerous_goods: bool = False
    source: str = "unspecified"

    def __post_init__(self) -> None:
        for name in ("vessel_id", "source"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        for name in ("length_m", "beam_m", "draught_m"):
            _finite_positive(name, getattr(self, name))
        _optional_positive("air_draught_m", self.air_draught_m)
        _strict_bool("carries_dangerous_goods", self.carries_dangerous_goods)

    @classmethod
    def from_reference(
        cls,
        reference: VesselReference,
        *,
        is_loaded: bool,
        air_draught_m: float | None = None,
        carries_dangerous_goods: bool = False,
        vessel_id: str | None = None,
    ) -> NavigabilityVessel:
        """Use the full loaded draught for any loaded trip, which is conservative."""
        _strict_bool("is_loaded", is_loaded)
        draught = reference.draught_loaded_m if is_loaded else reference.draught_empty_m
        state = "loaded" if is_loaded else "empty"
        return cls(
            vessel_id=vessel_id or f"{reference.vessel_rws_class}-{state}",
            length_m=reference.length_m,
            beam_m=reference.beam_m,
            draught_m=draught,
            air_draught_m=air_draught_m,
            carries_dangerous_goods=carries_dangerous_goods,
            source=f"vessel_reference:{reference.vessel_rws_class}:{state}",
        )


@dataclass(frozen=True, slots=True)
class NavigabilityMargins:
    """Scenario safety margins added to the vessel value before comparison."""

    under_keel_clearance_m: float = 0.0
    air_clearance_m: float = 0.0
    beam_clearance_m: float = 0.0

    def __post_init__(self) -> None:
        for name in ("under_keel_clearance_m", "air_clearance_m", "beam_clearance_m"):
            _finite_non_negative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class NavigabilityFinding:
    """A blocking or undetermined check; navigable checks produce no finding."""

    arc_id: str
    restriction: RestrictionType
    status: NavigabilityStatus
    source: str
    structure_id: str | None = None
    structure_type: StructureType | None = None
    required_m: float | None = None
    limit_m: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arc_id", _required_text("arc_id", self.arc_id))
        object.__setattr__(self, "source", _required_text("source", self.source))
        object.__setattr__(
            self, "restriction", _enum_value(RestrictionType, "restriction", self.restriction)
        )
        status = _enum_value(NavigabilityStatus, "status", self.status)
        if status is NavigabilityStatus.NAVIGABLE:
            raise ValidationError("A finding must be undetermined or not navigable")
        object.__setattr__(self, "status", status)
        for name in ("required_m", "limit_m"):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise ValidationError(f"{name} must be finite; received {value!r}")


@dataclass(frozen=True, slots=True)
class ArcNavigability:
    """Navigability of one arc for one vessel state."""

    arc_id: str
    findings: tuple[NavigabilityFinding, ...] = ()

    @property
    def status(self) -> NavigabilityStatus:
        return worst_status(tuple(finding.status for finding in self.findings))


@dataclass(frozen=True, slots=True)
class RouteNavigability:
    """Navigability of every traversal of a supplied route, in route order."""

    route_id: str
    vessel_id: str
    arcs: tuple[ArcNavigability, ...]

    @property
    def status(self) -> NavigabilityStatus:
        return worst_status(tuple(arc.status for arc in self.arcs))

    @property
    def findings(self) -> tuple[NavigabilityFinding, ...]:
        return tuple(finding for arc in self.arcs for finding in arc.findings)

    @property
    def first_blocking_sequence(self) -> int | None:
        """Zero-based position of the first not-navigable traversal, if any."""
        for sequence, arc in enumerate(self.arcs):
            if arc.status is NavigabilityStatus.NOT_NAVIGABLE:
                return sequence
        return None
