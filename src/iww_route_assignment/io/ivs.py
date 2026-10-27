"""Public IVS adapters and flat request/rejection records.

Source layouts live in ivs_sources; supplied directed edges and sequences live in
routes. No observed journey distance, speed or counting point feeds costing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import (
    AppearanceRule,
    ArcCostRequest,
    TripRejection,
)
from ..exceptions import ValidationError
from ._frames import (
    integer_value,
    read_csv_table,
    require_columns,
    text_value,
)
from .ivs_sources import (
    IVS_COLUMNS as IVS_COLUMNS,
)
from .ivs_sources import (
    IvsReadResult as IvsReadResult,
)
from .ivs_sources import (
    ivs_trips_from_frame as ivs_trips_from_frame,
)
from .ivs_sources import (
    load_ivs_trips_csv as load_ivs_trips_csv,
)
from .ivs_sources import (
    read_ivs_frame as read_ivs_frame,
)
from .routes import (
    load_arc_profiles_csv as load_arc_profiles_csv,
)
from .routes import (
    load_route_sequences_csv as load_route_sequences_csv,
)
from .routes import (
    load_trip_routes_csv as load_trip_routes_csv,
)

ARC_REQUEST_COLUMNS: tuple[str, ...] = (
    "case_row_id",
    "source_row_id",
    "route_id",
    "sequence",
    "route_arc_count",
    "arc_id",
    "from_node_id",
    "to_node_id",
    "appearance",
    "vessel_rws_class",
    "waterway_cemt_class",
    "loading_ratio",
    "distance_km",
    "realised_speed_km_per_h",
    "cargo_t",
    "is_loaded",
)
"""Columns the generic case runner requires, in a stable order."""


VESSEL_TYPE_COLUMNS = {"ship_type_id", "rws_class"}
APPEARANCE_MAP_COLUMNS = {"nstr_chapter", "appearance", "source"}


def load_vessel_type_map_csv(path: str | Path) -> dict[int, str]:
    """Load the numeric vessel-type to RWS-class mapping, rejecting conflicts."""
    table = read_csv_table(path)
    require_columns(table, VESSEL_TYPE_COLUMNS, "vessel-type mapping")
    mapping: dict[int, str] = {}
    for _, row in table.iterrows():
        vessel_type_id = integer_value(row, "ship_type_id")
        rws_class = text_value(row, "rws_class").strip()
        if not rws_class:
            raise ValidationError(f"Empty rws_class for ship_type_id={vessel_type_id}")
        if vessel_type_id in mapping and mapping[vessel_type_id] != rws_class:
            raise ValidationError(
                f"Conflicting vessel classes for ship_type_id={vessel_type_id}: "
                f"{mapping[vessel_type_id]!r} and {rws_class!r}"
            )
        mapping[vessel_type_id] = rws_class
    if not mapping:
        raise ValidationError("Vessel-type mapping contains no usable rows")
    return mapping


def load_appearance_map_csv(path: str | Path) -> dict[str, AppearanceRule]:
    """Load an NSTR chapter to appearance mapping with an explicit source column."""
    table = read_csv_table(path)
    require_columns(table, APPEARANCE_MAP_COLUMNS, "appearance mapping")
    mapping: dict[str, AppearanceRule] = {}
    for _, row in table.iterrows():
        chapter = text_value(row, "nstr_chapter").strip()
        if len(chapter) != 1 or not chapter.isdigit():
            raise ValidationError(f"nstr_chapter must be a single digit; received {chapter!r}")
        rule = AppearanceRule(
            appearance=text_value(row, "appearance"), source=text_value(row, "source")
        )
        if chapter in mapping and mapping[chapter] != rule:
            raise ValidationError(
                f"Conflicting appearance rule for nstr_chapter {chapter!r}: "
                f"{mapping[chapter]!r} and {rule!r}"
            )
        mapping[chapter] = rule
    if not mapping:
        raise ValidationError("Appearance mapping contains no usable rows")
    return mapping


def arc_requests_to_frame(requests: Iterable[ArcCostRequest]) -> pd.DataFrame:
    """Build the runner arc-request table, keeping provenance columns at the end."""
    records = [_arc_request_to_record(request) for request in requests]
    if not records:
        raise ValidationError("No arc-cost requests to write")
    frame = pd.DataFrame.from_records(records)
    provenance = [column for column in frame.columns if column not in ARC_REQUEST_COLUMNS]
    return frame[[*ARC_REQUEST_COLUMNS, *provenance]]


def trip_rejections_to_frame(rejections: Iterable[TripRejection]) -> pd.DataFrame:
    """Build a flat rejection table with reason codes and detail messages."""
    records = [
        {
            "source_row_id": rejection.source_row_id,
            "reason": rejection.reason.value,
            "detail": rejection.detail,
        }
        for rejection in rejections
    ]
    return pd.DataFrame.from_records(
        records, columns=["source_row_id", "reason", "detail"] if not records else None
    )


def _arc_request_to_record(request: ArcCostRequest) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in fields(request):
        value = getattr(request, field.name)
        record[field.name] = "; ".join(value) if field.name == "notes" else value
    return record
