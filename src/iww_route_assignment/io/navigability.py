"""CSV adapters for navigability restrictions, vessel states and flat results."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from ..domain.enums import StructureType
from ..domain.navigability import (
    LIMIT_FIELDS,
    ArcRestriction,
    NavigabilityVessel,
    RouteNavigability,
)
from ..exceptions import ValidationError
from ._frames import number_value, optional_text_value, read_csv_table, require_columns, text_value

TRUE_VALUES = frozenset({"true", "1", "yes"})
FALSE_VALUES = frozenset({"false", "0", "no"})

ROUTE_SUMMARY_COLUMNS = (
    "route_id",
    "vessel_id",
    "status",
    "arc_count",
    "first_blocking_sequence",
    "finding_count",
)
FINDING_COLUMNS = (
    "route_id",
    "vessel_id",
    "sequence",
    "arc_id",
    "structure_id",
    "structure_type",
    "restriction",
    "status",
    "required_m",
    "limit_m",
    "source",
)


def _optional_number(row: pd.Series, column: str) -> float | None:
    if column not in row.index or pd.isna(row[column]):
        return None
    return number_value(row, column)


def _optional_bool(row: pd.Series, column: str, default: bool) -> bool:
    text = optional_text_value(row, column).strip().lower()
    if not text:
        return default
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValidationError(f"Column {column!r} must be true or false; received {text!r}")


def _structure_type(row: pd.Series) -> StructureType:
    text = text_value(row, "structure_type").strip().lower()
    try:
        return StructureType(text)
    except ValueError as error:
        allowed = ", ".join(member.value for member in StructureType)
        raise ValidationError(
            f"Column 'structure_type' must be one of {allowed}; received {text!r}"
        ) from error


def load_arc_restrictions_csv(path: str | Path) -> tuple[ArcRestriction, ...]:
    """Read structure limits. Blank limits mean no limit; blank flags use permissive defaults."""
    table = read_csv_table(path)
    require_columns(
        table, {"arc_id", "structure_id", "structure_type", "source"}, "arc restrictions"
    )
    return tuple(
        ArcRestriction(
            arc_id=text_value(row, "arc_id"),
            structure_id=text_value(row, "structure_id"),
            structure_type=_structure_type(row),
            source=text_value(row, "source"),
            **{name: _optional_number(row, name) for name in LIMIT_FIELDS},
            dangerous_goods_permitted=_optional_bool(row, "dangerous_goods_permitted", True),
            is_open=_optional_bool(row, "is_open", True),
        )
        for _, row in table.iterrows()
    )


def load_navigability_vessels_csv(path: str | Path) -> tuple[NavigabilityVessel, ...]:
    """Read vessel states per trip class; duplicate identifiers are rejected."""
    table = read_csv_table(path)
    require_columns(table, {"vessel_id", "length_m", "beam_m", "draught_m"}, "navigability vessels")
    result = tuple(
        NavigabilityVessel(
            vessel_id=text_value(row, "vessel_id"),
            length_m=number_value(row, "length_m"),
            beam_m=number_value(row, "beam_m"),
            draught_m=number_value(row, "draught_m"),
            air_draught_m=_optional_number(row, "air_draught_m"),
            carries_dangerous_goods=_optional_bool(row, "carries_dangerous_goods", False),
            source=optional_text_value(row, "source", "unspecified") or "unspecified",
        )
        for _, row in table.iterrows()
    )
    if len({vessel.vessel_id for vessel in result}) != len(result):
        raise ValidationError("Duplicate vessel_id in navigability vessels")
    return result


def route_navigability_to_frames(
    results: Iterable[RouteNavigability],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one summary row per route and vessel, and one row per finding."""
    summaries = []
    findings = []
    for result in results:
        summaries.append(
            {
                "route_id": result.route_id,
                "vessel_id": result.vessel_id,
                "status": result.status.value,
                "arc_count": len(result.arcs),
                "first_blocking_sequence": result.first_blocking_sequence,
                "finding_count": len(result.findings),
            }
        )
        for sequence, arc in enumerate(result.arcs):
            for finding in arc.findings:
                findings.append(
                    {
                        "route_id": result.route_id,
                        "vessel_id": result.vessel_id,
                        "sequence": sequence,
                        "arc_id": finding.arc_id,
                        "structure_id": finding.structure_id,
                        "structure_type": (
                            None if finding.structure_type is None else finding.structure_type.value
                        ),
                        "restriction": finding.restriction.value,
                        "status": finding.status.value,
                        "required_m": finding.required_m,
                        "limit_m": finding.limit_m,
                        "source": finding.source,
                    }
                )
    summary_frame = pd.DataFrame(summaries, columns=list(ROUTE_SUMMARY_COLUMNS))
    summary_frame["first_blocking_sequence"] = summary_frame["first_blocking_sequence"].astype(
        "Int64"
    )
    return summary_frame, pd.DataFrame(findings, columns=list(FINDING_COLUMNS))
