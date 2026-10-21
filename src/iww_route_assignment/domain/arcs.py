"""Supplied directed arcs and routes, independent of any network or route search."""

from dataclasses import dataclass

from ..exceptions import ValidationError
from .classifications import normalise_cemt_class
from .models import ArcTravelInput, _required_text

TIME_FIELDS = (
    "lock_service_time_h",
    "lock_waiting_time_h",
    "bridge_service_time_h",
    "real_delay_time_h",
    "calibration_delay_time_h",
    "relaxation_penalty_time_h",
)


@dataclass(frozen=True, slots=True)
class ArcProfile:
    """A directed edge and supplied travel snapshot; not a permanent speed model."""

    arc_id: str
    from_node_id: str
    to_node_id: str
    distance_km: float
    waterway_cemt_class: str
    realised_speed_km_per_h: float
    source: str
    lock_service_time_h: float = 0.0
    lock_waiting_time_h: float = 0.0
    bridge_service_time_h: float = 0.0
    real_delay_time_h: float = 0.0
    calibration_delay_time_h: float = 0.0
    relaxation_penalty_time_h: float = 0.0

    def __post_init__(self) -> None:
        for name in ("arc_id", "from_node_id", "to_node_id", "source"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(
            self, "waterway_cemt_class", normalise_cemt_class(self.waterway_cemt_class)
        )
        self.travel_input()

    def travel_input(self) -> ArcTravelInput:
        """Validate and return supplied conditions, including zero-distance structures."""
        return ArcTravelInput(
            self.distance_km,
            self.realised_speed_km_per_h,
            **{name: getattr(self, name) for name in TIME_FIELDS},
        )


@dataclass(frozen=True, slots=True)
class RouteSequence:
    """One supplied connected route; repeated arcs remain distinct traversals."""

    route_id: str
    origin_locode: str
    destination_locode: str
    arcs: tuple[ArcProfile, ...]
    source: str

    def __post_init__(self) -> None:
        for name in ("route_id", "origin_locode", "destination_locode", "source"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        for name in ("origin_locode", "destination_locode"):
            object.__setattr__(self, name, getattr(self, name).upper())
        object.__setattr__(self, "arcs", tuple(self.arcs))
        if not self.arcs:
            raise ValidationError(
                "Route has no arcs; represent same-node trips with an explicit zero-distance arc"
            )
        for previous, following in zip(self.arcs, self.arcs[1:], strict=False):
            if previous.to_node_id != following.from_node_id:
                raise ValidationError(
                    f"Disconnected directed route {self.route_id!r}: "
                    f"{previous.arc_id!r} -> {following.arc_id!r}"
                )


@dataclass(frozen=True, slots=True)
class TripRoute:
    """Explicit association of a source trip with a supplied route."""

    source_row_id: str
    route_id: str

    def __post_init__(self) -> None:
        for name in ("source_row_id", "route_id"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
