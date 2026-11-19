"""Lock complexes and their chambers: the values the lock queue simulation needs."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..exceptions import ValidationError

DEFAULT_OPERATION_TIME_MIN = 30.0
"""Operation time of a chamber without a recorded time: one passage from side to side."""


def _positive(name: str, value: float) -> None:
    if not isfinite(value) or value <= 0:
        raise ValidationError(f"{name} must be finite and positive; received {value!r}")


@dataclass(frozen=True, slots=True)
class ChamberSpec:
    """One lock chamber.

    ``length_m`` is the usable length and ``width_m`` the gate width. ``operation_time_s`` is
    the time the chamber needs to move vessels from one side to the other. The ``*_source``
    fields record where a value came from, so defaults and derived values stay visible.
    """

    chamber_id: str
    length_m: float
    width_m: float
    operation_time_s: float
    dimension_source: str = "attributes"
    operation_time_source: str = "otimemin"

    def __post_init__(self) -> None:
        if not self.chamber_id.strip():
            raise ValidationError("chamber_id must not be empty")
        for name in ("length_m", "width_m", "operation_time_s"):
            _positive(name, getattr(self, name))

    @property
    def area_m2(self) -> float:
        return self.length_m * self.width_m

    def fits(self, length_m: float, beam_m: float) -> bool:
        """Whether a vessel of this length and beam fits in the chamber on its own."""
        return length_m <= self.length_m and beam_m <= self.width_m


@dataclass(frozen=True, slots=True)
class LockComplex:
    """A lock complex: one or more chambers at one place.

    ``lon`` and ``lat`` locate the complex in EPSG:4326. They are None when the lock file has
    no point for it. A complex without usable chambers cannot be simulated.
    """

    lock_id: str
    name: str
    chambers: tuple[ChamberSpec, ...]
    lon: float | None = None
    lat: float | None = None

    def __post_init__(self) -> None:
        if not self.lock_id.strip():
            raise ValidationError("lock_id must not be empty")
        ids = [chamber.chamber_id for chamber in self.chambers]
        if len(set(ids)) != len(ids):
            raise ValidationError(f"Duplicate chamber ids in lock {self.lock_id!r}")
        if (self.lon is None) != (self.lat is None):
            raise ValidationError(f"Lock {self.lock_id!r} needs both lon and lat, or neither")

    @property
    def chamber_count(self) -> int:
        return len(self.chambers)

    @property
    def mean_operation_time_s(self) -> float:
        """Average operation time over the chambers, for estimates at lock level."""
        return sum(c.operation_time_s for c in self.chambers) / len(self.chambers)

    def largest_chamber(self) -> ChamberSpec:
        """The chamber with the largest floor area. An oversized vessel uses this one."""
        return max(self.chambers, key=lambda chamber: chamber.area_m2)

    def chambers_fitting(self, length_m: float, beam_m: float) -> tuple[ChamberSpec, ...]:
        return tuple(c for c in self.chambers if c.fits(length_m, beam_m))
