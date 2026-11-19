"""Lock complexes for the queue simulation, read from the EuRIS lock and chamber files.

Inputs, both from ``dropins-euris-detailed``:

- ``locks.geoparquet``: one point per lock complex, with ``id`` and ``name``.
- ``chambers.geoparquet``: one polygon per chamber, linked to its complex by ``lock_id``.
  ``dim_usable_length`` and ``dim_gate_width`` (m) give the size and ``otimemin`` the
  average operation time in minutes.

A missing size is derived from the chamber polygon: the long and short side of its minimum
rotated rectangle. A missing operation time becomes the default. Every substitution is counted
in the report, and a chamber without any usable size is skipped and counted.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from ..domain.locks import DEFAULT_OPERATION_TIME_MIN, ChamberSpec, LockComplex
from ..exceptions import ValidationError
from ._frames import require_columns
from .euris import EurisStructures, _positive, _text, load_euris_structures

logger = logging.getLogger(__name__)

POLYGON_CRS = "EPSG:3035"
"""Equal-area metric CRS used to measure chamber polygons."""

LOCK_FILE = "locks.geoparquet"
CHAMBER_FILE = "chambers.geoparquet"
CHAMBER_COLUMNS = {"lock_id", "dim_gate_width", "dim_usable_length"}


@dataclass(frozen=True, slots=True, eq=False)
class LockData:
    """Lock complexes with chambers, the sections they sit on and a report."""

    complexes: Mapping[str, LockComplex]
    section_locks: Mapping[str, tuple[str, ...]]
    report: Mapping[str, Any]


def _read_geo_table(path: Path) -> pd.DataFrame:
    try:
        table: pd.DataFrame = gpd.read_parquet(path)
    except (ValueError, TypeError):
        table = pd.read_parquet(path)
    return table


def _geometries(table: pd.DataFrame) -> list[BaseGeometry | None]:
    """Geometries in row order, or None per row when the table has no geometry column."""
    if not isinstance(table, gpd.GeoDataFrame) or table.geometry.name not in table.columns:
        return [None] * len(table)
    return [g if g is not None and not g.is_empty else None for g in table.geometry]


def _crs_of(table: pd.DataFrame) -> Any:
    return table.crs if isinstance(table, gpd.GeoDataFrame) and table.crs else "EPSG:4326"


def _polygon_size_m(polygon: BaseGeometry, crs: Any) -> tuple[float, float] | None:
    """Long and short side of the minimum rotated rectangle, in metres."""
    if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
        return None
    projected = gpd.GeoSeries([polygon], crs=crs).to_crs(POLYGON_CRS).iloc[0]
    rectangle = projected.minimum_rotated_rectangle
    if rectangle.geom_type != "Polygon":
        return None
    xy = list(rectangle.exterior.coords)
    sides = sorted(
        float(shapely.distance(shapely.Point(a), shapely.Point(b)))
        for a, b in zip(xy[:-1], xy[1:], strict=True)
    )
    long_side, short_side = sides[-1], sides[0]
    return (long_side, short_side) if short_side > 0 else None


def _point(geometry: BaseGeometry | None) -> tuple[float | None, float | None]:
    if geometry is None:
        return None, None
    point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
    return float(point.x), float(point.y)


def build_lock_complexes(
    locks: pd.DataFrame,
    chambers: pd.DataFrame,
    *,
    default_operation_time_min: float = DEFAULT_OPERATION_TIME_MIN,
) -> tuple[dict[str, LockComplex], dict[str, Any]]:
    """Build one :class:`LockComplex` per lock id that has at least one usable chamber.

    ``locks`` needs ``id`` and, optionally, ``name`` and a point geometry. ``chambers`` needs
    ``lock_id``, ``dim_gate_width`` and ``dim_usable_length``, and optionally ``otimemin``,
    ``id`` and a polygon geometry for the fallback size.
    """
    if default_operation_time_min <= 0:
        raise ValidationError("default_operation_time_min must be positive")
    require_columns(locks, {"id"}, LOCK_FILE)
    require_columns(chambers, CHAMBER_COLUMNS, CHAMBER_FILE)
    counts: Counter[str] = Counter()

    lock_rows: dict[str, tuple[str, float | None, float | None]] = {}
    for lock_id, name, geometry in zip(
        locks["id"],
        locks["name"] if "name" in locks.columns else [None] * len(locks),
        _geometries(locks),
        strict=True,
    ):
        if _text(lock_id):
            lon, lat = _point(geometry)
            lock_rows[_text(lock_id)] = (_text(name), lon, lat)

    crs = _crs_of(chambers)
    polygons = _geometries(chambers)
    by_lock: dict[str, list[ChamberSpec]] = defaultdict(list)
    for position, (row, polygon) in enumerate(
        zip(chambers.to_dict("records"), polygons, strict=True)
    ):
        lock_id = _text(row["lock_id"])
        if not lock_id:
            counts["chambers_without_lock_id"] += 1
            continue
        if lock_id not in lock_rows:
            counts["chambers_of_unknown_lock"] += 1
        length = _positive(row["dim_usable_length"])
        width = _positive(row["dim_gate_width"])
        sources = []
        if length is None or width is None:
            size = _polygon_size_m(polygon, crs) if polygon is not None else None
            if size is None:
                counts["chambers_skipped_no_dimensions"] += 1
                continue
            counts["dimensions_from_polygon"] += 1
            length = size[0] if length is None else length
            width = size[1] if width is None else width
            sources.append("polygon")
        minutes = _positive(row.get("otimemin"))
        if minutes is None:
            counts["operation_time_defaults"] += 1
        chamber_id = _text(row.get("id")) or f"{lock_id}-{len(by_lock[lock_id]) + 1}"
        if any(c.chamber_id == chamber_id for c in by_lock[lock_id]):
            chamber_id = f"{chamber_id}-{position}"
        by_lock[lock_id].append(
            ChamberSpec(
                chamber_id,
                length,
                width,
                (minutes if minutes is not None else default_operation_time_min) * 60.0,
                "polygon" if sources else "dim_usable_length,dim_gate_width",
                "otimemin" if minutes is not None else "default",
            )
        )

    complexes = {}
    for lock_id, specs in sorted(by_lock.items()):
        name, lon, lat = lock_rows.get(lock_id, ("", None, None))
        if lon is None:
            counts["locks_without_point"] += 1
        complexes[lock_id] = LockComplex(lock_id, name, tuple(specs), lon, lat)
    report = {
        "locks_in_file": len(lock_rows),
        "complexes": len(complexes),
        "chambers": sum(c.chamber_count for c in complexes.values()),
        "default_operation_time_min": default_operation_time_min,
        **dict(sorted(counts.items())),
    }
    return complexes, report


def load_lock_data(
    directory: str | Path,
    structures: EurisStructures | None = None,
    *,
    default_operation_time_min: float = DEFAULT_OPERATION_TIME_MIN,
) -> LockData:
    """Read the lock and chamber files and the section links from one folder."""
    folder = Path(directory)
    for name in (LOCK_FILE, CHAMBER_FILE):
        if not (folder / name).is_file():
            raise ValidationError(f"Required EuRIS file {folder / name} is missing")
    complexes, report = build_lock_complexes(
        _read_geo_table(folder / LOCK_FILE),
        _read_geo_table(folder / CHAMBER_FILE),
        default_operation_time_min=default_operation_time_min,
    )
    structures = structures or load_euris_structures(folder)
    linked = {lock for locks in structures.section_locks.values() for lock in locks}
    report["linked_locks_without_chambers"] = len(linked - set(complexes))
    logger.info("Read %d lock complexes with %d chambers", len(complexes), report["chambers"])
    return LockData(
        MappingProxyType(complexes), MappingProxyType(dict(structures.section_locks)), report
    )
