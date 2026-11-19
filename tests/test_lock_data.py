"""Lock complexes from the EuRIS lock and chamber files."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely import Point, box

from iww_route_assignment import ValidationError
from iww_route_assignment.domain import ChamberSpec, LockComplex
from iww_route_assignment.io.locks import build_lock_complexes, load_lock_data

# About 100 m by 12 m near 52 N, 5 E, in degrees.
LON, LAT = 5.0, 52.0
DEG_LON_PER_M = 1 / (111_320 * 0.6157)
DEG_LAT_PER_M = 1 / 111_320


def _polygon(length_m: float, width_m: float):
    return box(LON, LAT, LON + length_m * DEG_LON_PER_M, LAT + width_m * DEG_LAT_PER_M)


def _locks() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["1", "2", "3"], "name": ["One", "Two", "Three"]},
        geometry=[Point(5.0, 52.0), Point(5.1, 52.1), None],
        crs="EPSG:4326",
    )


def _chambers(rows: list[dict], polygons: list) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry=polygons, crs="EPSG:4326")


def test_chamber_values_come_from_attributes() -> None:
    chambers = _chambers(
        [
            {
                "id": "c1",
                "lock_id": "1",
                "dim_usable_length": 110.0,
                "dim_gate_width": 12.0,
                "otimemin": 20.0,
            },
            {
                "id": "c2",
                "lock_id": "1",
                "dim_usable_length": 90.0,
                "dim_gate_width": 10.0,
                "otimemin": 25.0,
            },
        ],
        [_polygon(10, 10), _polygon(10, 10)],
    )
    complexes, report = build_lock_complexes(_locks(), chambers)
    lock = complexes["1"]
    assert lock.name == "One" and (lock.lon, lock.lat) == (5.0, 52.0)
    assert [c.chamber_id for c in lock.chambers] == ["c1", "c2"]
    assert (lock.chambers[0].length_m, lock.chambers[0].width_m) == (110.0, 12.0)
    assert lock.chambers[0].operation_time_s == 1200.0
    assert lock.mean_operation_time_s == pytest.approx((1200.0 + 1500.0) / 2)
    assert lock.largest_chamber().chamber_id == "c1"
    assert report["chambers"] == 2 and "dimensions_from_polygon" not in report


def test_missing_dimensions_are_taken_from_the_polygon() -> None:
    chambers = _chambers(
        [
            {"lock_id": "1", "dim_usable_length": None, "dim_gate_width": 0.0, "otimemin": None},
            {"lock_id": "1", "dim_usable_length": 80.0, "dim_gate_width": None, "otimemin": 40.0},
        ],
        [_polygon(120, 14), _polygon(80, 9)],
    )
    complexes, report = build_lock_complexes(_locks(), chambers)
    first, second = complexes["1"].chambers
    assert first.length_m == pytest.approx(120, rel=0.02)
    assert first.width_m == pytest.approx(14, rel=0.02)
    assert first.dimension_source == "polygon"
    # An attribute that is present stays; only the missing one comes from the polygon.
    assert second.length_m == 80.0
    assert second.width_m == pytest.approx(9, rel=0.02)
    assert report["dimensions_from_polygon"] == 2


def test_operation_time_defaults_to_thirty_minutes() -> None:
    chambers = _chambers(
        [
            {
                "lock_id": "1",
                "dim_usable_length": 100.0,
                "dim_gate_width": 10.0,
                "otimemin": float("nan"),
            }
        ],
        [_polygon(10, 10)],
    )
    complexes, report = build_lock_complexes(_locks(), chambers)
    chamber = complexes["1"].chambers[0]
    assert chamber.operation_time_s == 1800.0 and chamber.operation_time_source == "default"
    assert report["operation_time_defaults"] == 1
    custom, _ = build_lock_complexes(_locks(), chambers, default_operation_time_min=10)
    assert custom["1"].chambers[0].operation_time_s == 600.0


def test_unusable_chambers_are_counted_not_guessed() -> None:
    chambers = _chambers(
        [
            {"lock_id": "1", "dim_usable_length": 100.0, "dim_gate_width": 10.0, "otimemin": 20.0},
            {"lock_id": "2", "dim_usable_length": None, "dim_gate_width": None, "otimemin": 20.0},
            {"lock_id": "", "dim_usable_length": 100.0, "dim_gate_width": 10.0, "otimemin": 20.0},
            {"lock_id": "9", "dim_usable_length": 70.0, "dim_gate_width": 9.0, "otimemin": 20.0},
        ],
        [_polygon(10, 10), None, _polygon(10, 10), _polygon(10, 10)],
    )
    complexes, report = build_lock_complexes(_locks(), chambers)
    assert sorted(complexes) == ["1", "9"]
    assert report["chambers_skipped_no_dimensions"] == 1
    assert report["chambers_without_lock_id"] == 1
    assert report["chambers_of_unknown_lock"] == 1
    assert complexes["9"].lon is None and complexes["9"].name == ""


def test_missing_columns_fail() -> None:
    with pytest.raises(ValidationError, match="dim_usable_length"):
        build_lock_complexes(_locks(), pd.DataFrame({"lock_id": ["1"], "dim_gate_width": [1]}))


def test_domain_values_are_validated() -> None:
    with pytest.raises(ValidationError):
        ChamberSpec("c", 0.0, 5.0, 60.0)
    spec = ChamberSpec("c", 100.0, 10.0, 60.0)
    assert spec.fits(100.0, 10.0) and not spec.fits(100.1, 5.0) and not spec.fits(50.0, 10.5)
    with pytest.raises(ValidationError, match="Duplicate chamber"):
        LockComplex("1", "x", (spec, spec))
    with pytest.raises(ValidationError, match="both lon and lat"):
        LockComplex("1", "x", (spec,), lon=5.0)


def _write_folder(folder: Path) -> None:
    chambers = _chambers(
        [
            {
                "id": "c1",
                "lock_id": "1",
                "dim_usable_length": 100.0,
                "dim_gate_width": 10.0,
                "otimemin": 15.0,
                "mdraughtcm": 300,
            }
        ],
        [_polygon(10, 10)],
    )
    chambers.to_parquet(folder / "chambers.geoparquet")
    _locks().to_parquet(folder / "locks.geoparquet")
    pd.DataFrame(
        {"section_id": ["S1", "S2"], "lock_id": ["1", "7"], "bridge_id": ["", ""]}
    ).to_parquet(folder / "edges.geoparquet")
    pd.DataFrame(
        {
            "bridge_id": ["b"],
            "dim_gate_width": [5.0],
            "dim_height": [4.0],
            "cl_height": [None],
            "mheightcmc": [None],
            "operable": [0],
        }
    ).to_parquet(folder / "openings.geoparquet")


def test_folder_is_read_with_section_links(tmp_path: Path) -> None:
    _write_folder(tmp_path)
    data = load_lock_data(tmp_path)
    assert list(data.complexes) == ["1"]
    assert data.complexes["1"].chambers[0].operation_time_s == 900.0
    assert data.section_locks["S1"] == ("1",)
    assert data.report["linked_locks_without_chambers"] == 1


def test_missing_lock_file_fails(tmp_path: Path) -> None:
    _write_folder(tmp_path)
    (tmp_path / "locks.geoparquet").unlink()
    with pytest.raises(ValidationError, match="locks.geoparquet"):
        load_lock_data(tmp_path)
