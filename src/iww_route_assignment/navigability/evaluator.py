"""Independent navigability evaluator.

It compares a vessel with physical limits and never reads or produces monetary values. Traffic
assignment turns its findings into route-choice penalties, see
:mod:`iww_route_assignment.assignment.penalties`. :func:`restrict_choice_weights` supports a
strict analysis in which a blocked arc cannot be used at any cost.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

import numpy as np
import numpy.typing as npt

from ..domain.arcs import RouteSequence
from ..domain.enums import NavigabilityStatus, RestrictionType
from ..domain.navigability import (
    ArcNavigability,
    ArcRestriction,
    NavigabilityFinding,
    NavigabilityMargins,
    NavigabilityVessel,
    RouteNavigability,
)
from ..exceptions import ValidationError

COMPARISON_TOLERANCE_M = 1e-9
"""Absorbs binary rounding in ``value + margin``; equality with a limit is navigable."""

RestrictionIndex = Mapping[str, tuple[ArcRestriction, ...]]

_NO_MARGINS = NavigabilityMargins()


def build_restriction_index(restrictions: Iterable[ArcRestriction]) -> RestrictionIndex:
    """Group restrictions by arc in a deterministic order; reject duplicate structures."""
    grouped: dict[str, list[ArcRestriction]] = {}
    seen: set[tuple[str, str]] = set()
    for restriction in restrictions:
        key = (restriction.arc_id, restriction.structure_id)
        if key in seen:
            raise ValidationError(
                f"Duplicate restriction for arc {key[0]!r} and structure {key[1]!r}"
            )
        seen.add(key)
        grouped.setdefault(restriction.arc_id, []).append(restriction)
    return MappingProxyType(
        {
            arc_id: tuple(
                sorted(rows, key=lambda row: (row.structure_type.value, row.structure_id))
            )
            for arc_id, rows in sorted(grouped.items())
        }
    )


def _dimension_finding(
    restriction: ArcRestriction,
    kind: RestrictionType,
    limit_m: float | None,
    vessel_value_m: float | None,
    margin_m: float,
) -> NavigabilityFinding | None:
    if limit_m is None:
        return None
    if vessel_value_m is None:
        return NavigabilityFinding(
            restriction.arc_id,
            kind,
            NavigabilityStatus.UNDETERMINED,
            restriction.source,
            restriction.structure_id,
            restriction.structure_type,
            None,
            limit_m,
        )
    required_m = vessel_value_m + margin_m
    if required_m <= limit_m + COMPARISON_TOLERANCE_M:
        return None
    return NavigabilityFinding(
        restriction.arc_id,
        kind,
        NavigabilityStatus.NOT_NAVIGABLE,
        restriction.source,
        restriction.structure_id,
        restriction.structure_type,
        required_m,
        limit_m,
    )


def _flag_finding(restriction: ArcRestriction, kind: RestrictionType) -> NavigabilityFinding:
    return NavigabilityFinding(
        restriction.arc_id,
        kind,
        NavigabilityStatus.NOT_NAVIGABLE,
        restriction.source,
        restriction.structure_id,
        restriction.structure_type,
    )


def evaluate_restriction(
    restriction: ArcRestriction,
    vessel: NavigabilityVessel,
    margins: NavigabilityMargins = _NO_MARGINS,
) -> tuple[NavigabilityFinding, ...]:
    """Check one structure; return only blocking or undetermined findings."""
    findings: list[NavigabilityFinding | None] = []
    if not restriction.is_open:
        findings.append(_flag_finding(restriction, RestrictionType.CLOSED))
    if vessel.carries_dangerous_goods and not restriction.dangerous_goods_permitted:
        findings.append(_flag_finding(restriction, RestrictionType.DANGEROUS_GOODS))
    findings.extend(
        (
            _dimension_finding(
                restriction,
                RestrictionType.LENGTH,
                restriction.max_length_m,
                vessel.length_m,
                0.0,
            ),
            _dimension_finding(
                restriction,
                RestrictionType.BEAM,
                restriction.max_beam_m,
                vessel.beam_m,
                margins.beam_clearance_m,
            ),
            _dimension_finding(
                restriction,
                RestrictionType.DRAUGHT,
                restriction.max_draught_m,
                vessel.draught_m,
                margins.under_keel_clearance_m,
            ),
            _dimension_finding(
                restriction,
                RestrictionType.AIR_DRAUGHT,
                restriction.max_air_draught_m,
                vessel.air_draught_m,
                margins.air_clearance_m,
            ),
        )
    )
    return tuple(finding for finding in findings if finding is not None)


def evaluate_arc(
    arc_id: str,
    vessel: NavigabilityVessel,
    index: RestrictionIndex,
    margins: NavigabilityMargins = _NO_MARGINS,
    *,
    require_coverage: bool = True,
) -> ArcNavigability:
    """Evaluate all structures on an arc. Uncovered arcs are undetermined by default."""
    restrictions = index.get(arc_id)
    if restrictions is None:
        if not require_coverage:
            return ArcNavigability(arc_id)
        return ArcNavigability(
            arc_id,
            (
                NavigabilityFinding(
                    arc_id,
                    RestrictionType.COVERAGE,
                    NavigabilityStatus.UNDETERMINED,
                    "restriction_index",
                ),
            ),
        )
    findings = tuple(
        finding
        for restriction in restrictions
        for finding in evaluate_restriction(restriction, vessel, margins)
    )
    return ArcNavigability(arc_id, findings)


def evaluate_route(
    route: RouteSequence,
    vessel: NavigabilityVessel,
    index: RestrictionIndex,
    margins: NavigabilityMargins = _NO_MARGINS,
    *,
    require_coverage: bool = True,
) -> RouteNavigability:
    """Evaluate every traversal of a supplied route; repeated arcs stay separate."""
    cache: dict[str, ArcNavigability] = {}
    results = []
    for arc in route.arcs:
        if arc.arc_id not in cache:
            cache[arc.arc_id] = evaluate_arc(
                arc.arc_id, vessel, index, margins, require_coverage=require_coverage
            )
        results.append(cache[arc.arc_id])
    return RouteNavigability(route.route_id, vessel.vessel_id, tuple(results))


def navigable_arc_mask(
    arc_ids: Sequence[str],
    vessel: NavigabilityVessel,
    index: RestrictionIndex,
    margins: NavigabilityMargins = _NO_MARGINS,
    *,
    require_coverage: bool = True,
    allow_undetermined: bool = False,
) -> npt.NDArray[np.bool_]:
    """Return one boolean per arc for a trip class, aligned with ``arc_ids``."""
    permitted = {NavigabilityStatus.NAVIGABLE}
    if allow_undetermined:
        permitted.add(NavigabilityStatus.UNDETERMINED)
    return np.fromiter(
        (
            evaluate_arc(arc_id, vessel, index, margins, require_coverage=require_coverage).status
            in permitted
            for arc_id in arc_ids
        ),
        dtype=np.bool_,
        count=len(arc_ids),
    )


def restrict_choice_weights(
    choice_weights: npt.ArrayLike, navigable_mask: npt.ArrayLike
) -> npt.NDArray[np.float64]:
    """Set blocked arcs to infinity. No finite cost or penalty can override a restriction."""
    weights = np.asarray(choice_weights, dtype=np.float64)
    mask = np.asarray(navigable_mask)
    if mask.dtype != np.bool_:
        raise ValidationError("navigable_mask must be boolean")
    if weights.shape != mask.shape:
        raise ValidationError(
            f"choice_weights shape {weights.shape} does not match mask shape {mask.shape}"
        )
    if not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValidationError("choice_weights must be finite and non-negative")
    return np.where(mask, weights, np.inf)
