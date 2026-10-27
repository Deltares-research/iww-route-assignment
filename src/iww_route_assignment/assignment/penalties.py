"""Turn navigability findings and route preferences into route-choice penalty hours.

Penalties enter route choice only, through ``relaxation_penalty_time_h``. Reported time and
reported cost never include them. Every penalty keeps the structure and limit behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import numpy.typing as npt

from ..domain.classifications import cemt_rank
from ..domain.enums import NavigabilityStatus, PenaltyType, RestrictionType, StructureType
from ..domain.navigability import NavigabilityFinding, NavigabilityVessel
from ..exceptions import ValidationError
from ..navigability import RestrictionIndex, evaluate_arc
from .network import AssignmentNetwork
from .settings import NavigabilityPenalties, RoutePreferences

_LIMIT_PENALTY = {
    RestrictionType.LENGTH: PenaltyType.LENGTH,
    RestrictionType.DRAUGHT: PenaltyType.DRAUGHT,
    RestrictionType.AIR_DRAUGHT: PenaltyType.AIR_DRAUGHT,
}


@dataclass(frozen=True, slots=True)
class PenaltyDetail:
    """One penalty on one arc for one vessel state, with the limit that caused it."""

    penalty_type: PenaltyType
    penalty_h: float
    source: str
    structure_id: str | None = None
    structure_type: StructureType | None = None
    required_m: float | None = None
    limit_m: float | None = None


@dataclass(frozen=True, slots=True, eq=False)
class PenaltyProfile:
    """Penalty hours per arc for one vessel state, by type and in total."""

    hours: MappingProxyType[PenaltyType, npt.NDArray[np.float64]]
    total_h: npt.NDArray[np.float64]
    details: MappingProxyType[int, tuple[PenaltyDetail, ...]]


def is_flat_beam_structure(structure_id: str | None, countries: frozenset[str]) -> bool:
    """EuRIS identifiers start with the two-letter country code."""
    return bool(structure_id) and str(structure_id)[:2].upper() in countries


def _tightest(findings: list[NavigabilityFinding]) -> NavigabilityFinding:
    return min(findings, key=lambda f: (f.limit_m, f.structure_id or ""))


def _navigability_details(
    findings: tuple[NavigabilityFinding, ...], penalties: NavigabilityPenalties
) -> list[PenaltyDetail]:
    blocking: dict[RestrictionType, list[NavigabilityFinding]] = {}
    undetermined: list[NavigabilityFinding] = []
    for finding in findings:
        if finding.status is NavigabilityStatus.UNDETERMINED:
            undetermined.append(finding)
        elif finding.restriction in (RestrictionType.BEAM, *_LIMIT_PENALTY):
            blocking.setdefault(finding.restriction, []).append(finding)
        else:
            raise ValidationError(
                f"Restriction {finding.restriction.value!r} on arc {finding.arc_id!r} has no "
                "route-choice penalty; supply only length, beam, draught and air-draught limits"
            )
    details = []
    for restriction, group in sorted(blocking.items()):
        worst = _tightest(group)
        if worst.required_m is None or worst.limit_m is None:
            raise ValidationError(f"Blocking finding on {worst.arc_id!r} has no dimensions")
        if restriction is RestrictionType.BEAM:
            penalty_type = PenaltyType.BEAM
            if is_flat_beam_structure(worst.structure_id, penalties.flat_beam_countries):
                hours = penalties.beam_h
            else:
                hours = penalties.beam_abroad_h_per_m * (worst.required_m - worst.limit_m)
        else:
            penalty_type = _LIMIT_PENALTY[restriction]
            hours = getattr(penalties, f"{penalty_type.value}_h")
        details.append(
            PenaltyDetail(
                penalty_type,
                hours,
                worst.source,
                worst.structure_id,
                worst.structure_type,
                worst.required_m,
                worst.limit_m,
            )
        )
    if undetermined:
        first = min(undetermined, key=lambda f: (f.restriction.value, f.structure_id or ""))
        details.append(
            PenaltyDetail(
                PenaltyType.UNDETERMINED,
                penalties.undetermined_h,
                first.source,
                first.structure_id,
                first.structure_type,
                None,
                first.limit_m,
            )
        )
    return details


def penalty_profile(
    network: AssignmentNetwork,
    restrictions: RestrictionIndex,
    vessel: NavigabilityVessel,
    vessel_cemt_class: str,
    penalties: NavigabilityPenalties,
    preferences: RoutePreferences,
) -> PenaltyProfile:
    """Return penalty hours on every arc for one vessel state.

    Arcs without restrictions get no navigability penalty. A penalty of 0 is kept in
    ``details`` so the outputs still show which limit was not met.
    """
    hours = {kind: np.zeros(network.edge_count) for kind in PenaltyType}
    details: dict[int, tuple[PenaltyDetail, ...]] = {}
    vessel_rank = cemt_rank(vessel_cemt_class)
    for index, edge in enumerate(network.edges):
        arc = evaluate_arc(
            edge.arc_id, vessel, restrictions, penalties.margins, require_coverage=False
        )
        found = _navigability_details(arc.findings, penalties)
        if edge.waterway_cemt_class is not None and vessel_rank > cemt_rank(
            edge.waterway_cemt_class
        ):
            found.append(
                PenaltyDetail(
                    PenaltyType.CEMT,
                    preferences.cemt_h,
                    "preference:vessel-cemt-above-waterway-cemt",
                    edge.section_code or edge.arc_id,
                    StructureType.WATERWAY,
                )
            )
        if edge.water_name.strip().casefold() in preferences.sea_water_names:
            found.append(
                PenaltyDetail(
                    PenaltyType.SEA,
                    preferences.sea_h,
                    "preference:sea-water-name",
                    edge.section_code or edge.arc_id,
                    StructureType.WATERWAY,
                )
            )
        if found:
            details[index] = tuple(found)
            for detail in found:
                hours[detail.penalty_type][index] += detail.penalty_h
    total = np.sum(np.vstack(list(hours.values())), axis=0)
    return PenaltyProfile(
        MappingProxyType({kind: values for kind, values in hours.items()}),
        total,
        MappingProxyType(details),
    )
