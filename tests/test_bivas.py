"""BIVAS reader on a tiny SQLite database with the real table and column names."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import ValidationError
from iww_route_assignment.io import (
    BIVAS_APPEARANCES,
    BIVAS_CEMT_COLUMNS,
    BIVAS_COST_COLUMNS,
    BIVAS_SHIP_TYPE_COLUMNS,
    read_bivas_cemt_classes,
    read_bivas_cost_rates,
    read_bivas_cost_sets,
    read_bivas_ship_type_table,
    read_bivas_ship_types,
)
from iww_route_assignment.io.bivas import KOSTENKENGETALLEN_2021

COST_COLUMNS = list(BIVAS_COST_COLUMNS)
SCHEMA = f"""
CREATE TABLE travel_costs (Id INTEGER, Description TEXT);
CREATE TABLE ship_types (
    ID INTEGER, Label TEXT, Description TEXT, CEMTTypeID INTEGER,
    "Width_10%__m" REAL, "Width_50%__m" REAL, "Width_90%__m" REAL,
    "Length_10%__m" REAL, "Length_50%__m" REAL, "Length_90%__m" REAL, EmptyDepth__m REAL
);
CREATE TABLE appearance_types (ID INTEGER, Description TEXT);
CREATE TABLE travel_cost_values (
    TravelCosts INTEGER, AppearanceTypeID INTEGER, ShipTypeID INTEGER,
    {", ".join(f"{name} REAL" for name in COST_COLUMNS)}
);
CREATE TABLE cemt_class (
    Id INTEGER, Description TEXT, Width__m REAL, CrossSection__m2 REAL, Depth__m REAL,
    MinimumAbsoluteUkc__m REAL, MinimumRelativeUkc__m REAL, Category TEXT
);
"""


def _cost_row(cost_set: int, appearance: int, ship: int, base: float) -> tuple[float, ...]:
    """Eleven cost values base+1 to base+11, in the order of BIVAS_COST_COLUMNS."""
    return (cost_set, appearance, ship, *(base + step for step in range(1, 12)))


def make_bivas_db(path: Path, statements: tuple[str, ...] = ()) -> Path:
    """Two cost sets, two ship types, two appearances and two waterway classes."""
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO travel_costs VALUES (?, ?)",
        [(1, "Kostenkengetallen 2017"), (3, "Kostenkengetallen 2021")],
    )
    # Deliberately not in ID order, to show that results are ordered by ID.
    connection.executemany(
        f"INSERT INTO ship_types VALUES ({', '.join('?' * 11)})",  # noqa: S608
        [
            (7, "BII-2B", "Duwstel", 2, 11.2, 11.4, 11.4, 160.0, 170.0, 175.0, 1.5),
            (2, "M1", "Spits", 1, 5.04, 5.06, 5.09, 38.62, 38.89, 39.53, 1.0),
        ],
    )
    connection.executemany(
        "INSERT INTO appearance_types VALUES (?, ?)", [(2, "Droge bulk"), (1, "Container")]
    )
    marks = ", ".join("?" * 14)
    connection.executemany(
        f"INSERT INTO travel_cost_values VALUES ({marks})",  # noqa: S608
        [
            _cost_row(3, 2, 7, 300.0),
            _cost_row(3, 2, 2, 200.0),
            _cost_row(3, 1, 2, 100.0),
            _cost_row(1, 2, 2, 900.0),
        ],
    )
    connection.executemany(
        "INSERT INTO cemt_class VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(2, "Va", 40.0, 180.0, 4.5, 0.3, 0.1, ""), (1, "I", 20.0, 50.0, 2.5, 0.2, 0.1, "canal")],
    )
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()
    return path


def test_cost_sets_are_read_by_id(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    assert read_bivas_cost_sets(db) == {
        1: "Kostenkengetallen 2017",
        3: "Kostenkengetallen 2021",
    }
    assert KOSTENKENGETALLEN_2021 == 3


def test_cost_rates_map_real_column_names_for_the_selected_set(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    rates = read_bivas_cost_rates(db)
    # Ordered by AppearanceTypeID then ShipTypeID: Container/M1, Droge bulk/M1, Droge bulk/BII-2B.
    assert rates["appearance"].tolist() == ["container", "droge_bulk", "droge_bulk"]
    assert rates["rws_class"].tolist() == ["M1", "M1", "BII-2B"]
    assert rates["ship_type_id"].tolist() == [2, 2, 7]
    # Column k of BIVAS_COST_COLUMNS holds base + k. The Container/M1 row has base 100.
    first = rates.iloc[0]
    assert first["labor_h"] == 101.0  # LaborCostsPerHour_Euro
    assert first["labor_wait_h"] == 102.0  # LaborCostsPerHourWaitingOnLoad_Euro
    assert first["maint_km"] == 104.0  # MaintenanceCostsPerKilometer_Euro
    assert first["interest_pct_h"] == 106.0  # InterestCostsPerPercentPerHour_Euro
    assert first["unload_t"] == 111.0  # UnloadCostsPerTon_Euro
    assert list(rates.columns) == [
        "appearance",
        "ship_type_id",
        "rws_class",
        *BIVAS_COST_COLUMNS.values(),
        "price_level",
        "source",
    ]
    assert set(rates["price_level"]) == {2021}
    assert rates["source"].iloc[0] == (
        "BIVAS travel_cost_values, TravelCosts=3 (Kostenkengetallen 2021)"
    )
    assert set(BIVAS_APPEARANCES.values()) >= set(rates["appearance"])


def test_another_cost_set_is_selected_explicitly(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    rates = read_bivas_cost_rates(db, cost_set=1)
    assert rates["labor_h"].tolist() == [901.0]
    assert rates["price_level"].tolist() == [2017]


def test_cost_set_without_a_year_has_no_price_level(tmp_path: Path) -> None:
    db = make_bivas_db(
        tmp_path / "Bivas.db",
        (
            "INSERT INTO travel_costs VALUES (5, 'Proef')",
            "INSERT INTO travel_cost_values (TravelCosts, AppearanceTypeID, ShipTypeID, "
            + ", ".join(COST_COLUMNS)
            + ") VALUES (5, 1, 2, "
            + ", ".join("1" for _ in COST_COLUMNS)
            + ")",
        ),
    )
    assert read_bivas_cost_rates(db, cost_set=5)["price_level"].isna().all()


def test_unknown_or_empty_cost_set_is_rejected(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db", ("INSERT INTO travel_costs VALUES (4, 'Leeg 2030')",))
    with pytest.raises(ValidationError, match="Cost set 9 is not in travel_costs"):
        read_bivas_cost_rates(db, cost_set=9)
    with pytest.raises(ValidationError, match="no rows for cost set 4"):
        read_bivas_cost_rates(db, cost_set=4)


def test_missing_database_and_missing_table_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="BIVAS database not found"):
        read_bivas_cost_sets(tmp_path / "missing.db")
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(ValidationError, match="query failed"):
        read_bivas_cost_sets(empty)


def test_unknown_appearance_is_rejected(tmp_path: Path) -> None:
    db = make_bivas_db(
        tmp_path / "Bivas.db",
        (
            "INSERT INTO appearance_types VALUES (9, 'Vreemd')",
            "INSERT INTO travel_cost_values (TravelCosts, AppearanceTypeID, ShipTypeID) "
            "VALUES (3, 9, 2)",
        ),
    )
    with pytest.raises(ValidationError, match="Unknown BIVAS appearance types.*Vreemd"):
        read_bivas_cost_rates(db)


def test_duplicate_cost_rows_are_rejected_not_resolved_by_order(tmp_path: Path) -> None:
    db = make_bivas_db(
        tmp_path / "Bivas.db",
        (
            "INSERT INTO travel_cost_values (TravelCosts, AppearanceTypeID, ShipTypeID) "
            "VALUES (3, 1, 2)",
        ),
    )
    with pytest.raises(ValidationError, match="duplicate appearance and ship type"):
        read_bivas_cost_rates(db)


def test_cemt_classes_are_renamed_and_ordered(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    classes = read_bivas_cemt_classes(db)
    assert list(classes.columns) == list(BIVAS_CEMT_COLUMNS.values())
    assert classes["cemt_class_id"].tolist() == [1, 2]
    assert classes["label"].tolist() == ["I", "Va"]
    assert classes.loc[0, "std_width_m"] == 20.0  # Width__m
    assert classes.loc[0, "keel_clearance_abs_m"] == 0.2  # MinimumAbsoluteUkc__m
    assert classes.loc[0, "waterway_type"] == "canal"
    # An empty Category is a missing value, not an empty label.
    assert pd.isna(classes.loc[1, "waterway_type"])


def test_empty_cemt_table_is_rejected(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db", ("DELETE FROM cemt_class",))
    with pytest.raises(ValidationError, match="cemt_class has no rows"):
        read_bivas_cemt_classes(db)


def test_ship_types_are_ordered_and_unique(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    ships = read_bivas_ship_types(db)
    assert ships.to_dict("list") == {"ship_type_id": [2, 7], "rws_class": ["M1", "BII-2B"]}
    duplicated = make_bivas_db(
        tmp_path / "duplicated.db", ("INSERT INTO ship_types (ID, Label) VALUES (8, 'M1')",)
    )
    with pytest.raises(ValidationError, match="duplicate labels"):
        read_bivas_ship_types(duplicated)
    with pytest.raises(ValidationError, match="duplicate labels"):
        read_bivas_ship_type_table(duplicated)


def test_ship_type_table_keeps_dimensions_and_names_the_waterway_class(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    table = read_bivas_ship_type_table(db)
    assert list(table.columns) == [*BIVAS_SHIP_TYPE_COLUMNS.values(), "cemt_class"]
    assert table["rws_class"].tolist() == ["M1", "BII-2B"]  # ordered by ID
    # CEMTTypeID 1 and 2 are the cemt_class rows with Id 1 and 2.
    assert table["cemt_class"].tolist() == ["I", "Va"]
    first = table.iloc[0]
    assert (first["width_p10_m"], first["width_p50_m"], first["width_p90_m"]) == (5.04, 5.06, 5.09)
    assert (first["length_p50_m"], first["draught_empty_m"]) == (38.89, 1.0)
    unknown = make_bivas_db(tmp_path / "unknown.db", ("UPDATE ship_types SET CEMTTypeID = 99",))
    assert read_bivas_ship_type_table(unknown)["cemt_class"].isna().all()


def test_database_is_opened_read_only(tmp_path: Path) -> None:
    db = make_bivas_db(tmp_path / "Bivas.db")
    before = db.read_bytes()
    read_bivas_cost_rates(db)
    read_bivas_cemt_classes(db)
    read_bivas_ship_type_table(db)
    assert db.read_bytes() == before
