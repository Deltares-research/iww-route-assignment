"""Read IVS trips with their points for assignment, and write assignment outputs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from ..assignment import AssignmentResult, AssignmentSettings, AssignmentTrips
from ..domain.enums import DuplicateTripRule, IvsFormat
from ..domain.models import AppearanceRule
from ._frames import require_columns
from .ivs import trip_rejections_to_frame
from .ivs_extended import DEFAULT_SEGMENT_MAP
from .ivs_sources import read_ivs_frame

logger = logging.getLogger(__name__)

ASSIGNMENT_SEGMENT_MAP: Mapping[str, AppearanceRule] = {
    **DEFAULT_SEGMENT_MAP,
    "Overig": AppearanceRule("overig", "assumption:ivs-segment-overig-is-overig-v1"),
}
"""IVS segment to cost appearance. Adds ``Overig`` so every IVS segment can be costed."""

IVS_SK_CODE_CROSSWALK: Mapping[str, str] = {"B01": "BO1", "B02": "BO2", "B04": "BO4"}
"""Confirmed crosswalk: IVS writes the push-boat classes BO1, BO2 and BO4 with a zero.

Pass it as ``VesselCatalogue(class_aliases=IVS_SK_CODE_CROSSWALK)``. ``B03`` is not listed:
``BO3`` has no vessel reference, so mapping it would not route those trips.
"""

TRIP_ID_COLUMNS = {
    IvsFormat.EXTENDED_2026: ("Jaar", "Pseudo_ReisID"),
    IvsFormat.WEEKMONITOR: ("source_row_id",),
}

OUTPUT_FILES = {
    "trips": "voyages.parquet",
    "trip_edges": "voyage_edges.parquet",
    "intensity": "intensity.geoparquet",
    "gpkg": "assignment.gpkg",
    "rejections": "trip_rejections.csv",
    "summary": "summary.json",
}


def _row_identifiers(frame: pd.DataFrame, layout: IvsFormat, dataset: str) -> pd.Series:
    """Rebuild the trip identifiers that :func:`read_ivs_frame` assigns, per row."""
    if layout is IvsFormat.EXTENDED_2026:
        year = pd.to_numeric(frame["Jaar"], errors="coerce").astype("Int64").astype(str)
        trip = frame["Pseudo_ReisID"].astype("string").str.strip()
        return dataset + ":" + year + ":" + trip
    if "source_row_id" in frame.columns:
        return frame["source_row_id"].astype("string").str.strip()
    return pd.Series([f"{dataset}:{i}" for i in range(len(frame))], index=frame.index)


def _drop_duplicates(
    frame: pd.DataFrame, layout: IvsFormat, rule: DuplicateTripRule
) -> tuple[pd.DataFrame, int]:
    columns = [c for c in TRIP_ID_COLUMNS[layout] if c in frame.columns]
    if not columns or rule is DuplicateTripRule.FAIL:
        return frame, 0
    duplicated = frame.duplicated(columns, keep=False)
    return frame[~duplicated], int(duplicated.sum())


def read_assignment_trips(
    frame: pd.DataFrame,
    *,
    source_dataset: str,
    format: IvsFormat | str = IvsFormat.EXTENDED_2026,
    cargo_weight_unit: str | None = None,
    drop_export_index: bool = False,
    duplicate_trip_ids: DuplicateTripRule | str = DuplicateTripRule.FAIL,
    settings: AssignmentSettings | None = None,
    segment_map: Mapping[str, AppearanceRule] | None = None,
) -> AssignmentTrips:
    """Validate IVS rows and attach origin and destination points.

    Trips are validated by :func:`read_ivs_frame`, so units and identifiers follow the
    cost case. ``duplicate_trip_ids="exclude"`` removes every row of a repeated trip id;
    no row is chosen over another. Point columns hold WKT in ``settings.points_crs``.
    A missing or unreadable point leaves the trip without that point.
    """
    settings = settings or AssignmentSettings()
    layout = IvsFormat(format)
    rule = DuplicateTripRule(duplicate_trip_ids)
    require_columns(frame, {settings.origin_column, settings.destination_column}, "IVS trips")
    kept, excluded = _drop_duplicates(frame, layout, rule)
    if excluded:
        logger.warning("Excluded %d rows with repeated trip ids", excluded)
    parsed = read_ivs_frame(
        kept,
        source_dataset=source_dataset,
        format=layout,
        cargo_weight_unit=cargo_weight_unit,
        segment_map=ASSIGNMENT_SEGMENT_MAP if segment_map is None else segment_map,
        drop_export_index=drop_export_index,
    )
    identifiers = _row_identifiers(kept, layout, source_dataset.strip())
    position = pd.Series(range(len(kept)), index=identifiers.to_numpy())
    rows = position.loc[[trip.source_row_id for trip in parsed.trips]].to_numpy()

    def points(column: str) -> gpd.GeoSeries:
        text = kept[column].iloc[rows].astype("string").str.strip()
        text = text.where(text.notna() & (text != ""), None).astype(object)
        return gpd.GeoSeries.from_wkt(text.to_numpy(), crs=settings.points_crs, on_invalid="ignore")

    return AssignmentTrips(
        parsed.trips,
        points(settings.origin_column),
        points(settings.destination_column),
        parsed.rejections,
        excluded,
    )


def load_assignment_trips_csv(path: str | Path, **options: Any) -> AssignmentTrips:
    """Read a CSV export as text, then :func:`read_assignment_trips`."""
    source = Path(path)
    options.setdefault("source_dataset", source.stem)
    return read_assignment_trips(pd.read_csv(source, dtype="string"), **options)


def to_record(value: Any) -> Any:
    """Turn settings dataclasses, enums and sets into JSON-ready values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_record(getattr(value, f.name)) for f in fields(value) if f.init}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset | set):
        return sorted(to_record(v) for v in value)
    if isinstance(value, Mapping):
        return {str(k): to_record(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [to_record(v) for v in value]
    return value


def write_assignment_outputs(
    result: AssignmentResult,
    directory: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Write trips, rejected rows, trip edges, intensity, penalised edges and a summary.

    ``assignment.gpkg`` holds the layers ``intensity`` and ``penalised_edges`` for GIS.
    ``summary.json`` holds status counts, coverage gaps and ``metadata``.
    """
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {name: folder / file for name, file in OUTPUT_FILES.items()}
    result.trips.to_parquet(paths["trips"], index=False)
    result.trip_edges.to_parquet(paths["trip_edges"], index=False)
    result.intensity.to_parquet(paths["intensity"], index=False)
    trip_rejections_to_frame(result.rejections).to_csv(paths["rejections"], index=False)
    paths["gpkg"].unlink(missing_ok=True)
    for layer, table in (("intensity", result.intensity), ("penalised_edges", result.penalised)):
        if not table.empty:
            table.to_file(paths["gpkg"], layer=layer, driver="GPKG")
    summary = dict(result.summary) | {"metadata": to_record(metadata or {})}
    paths["summary"].write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if result.intensity.empty:
        logger.warning("No trip was routed; intensity is empty")
    return paths


__all__ = [
    "ASSIGNMENT_SEGMENT_MAP",
    "IVS_SK_CODE_CROSSWALK",
    "OUTPUT_FILES",
    "load_assignment_trips_csv",
    "read_assignment_trips",
    "to_record",
    "write_assignment_outputs",
]
