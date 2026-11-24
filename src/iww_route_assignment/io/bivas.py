"""Read reference tables from a BIVAS SQLite database, by their real table and column names.

Source: the BIVAS database (``Bivas.db``). Tables read:

- ``travel_cost_values`` joined with ``ship_types`` and ``appearance_types``: the cost
  rates per cargo appearance and ship type, for one cost set of ``travel_costs``;
- ``cemt_class``: the waterway classes with their standard dimensions and keel clearance;
- ``ship_types``: the vessel classes, their waterway class and their width and length
  percentiles. This table has no loaded draught, height or capacity, so vessel dimensions
  come from the DTV ship-type file. BIVAS decides which classes have cost rates.

The database is opened read-only. Nothing is written back.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from ..exceptions import ValidationError

KOSTENKENGETALLEN_2021 = 3
"""``travel_costs.Id`` of the cost set "Kostenkengetallen 2021"."""

BIVAS_COST_COLUMNS: Mapping[str, str] = {
    "LaborCostsPerHour_Euro": "labor_h",
    "LaborCostsPerHourWaitingOnLoad_Euro": "labor_wait_h",
    "MaintenanceCostsPerHour_Euro": "maint_h",
    "MaintenanceCostsPerKilometer_Euro": "maint_km",
    "InsuranceCostsPerHour_Euro": "insurance_h",
    "InterestCostsPerPercentPerHour_Euro": "interest_pct_h",
    "DepreciationCostsPerHour_Euro": "depreciation_h",
    "HarborFeesPerHour_Euro": "harbor_h",
    "OtherCostsPerHour_Euro": "other_h",
    "LoadCostsPerTon_Euro": "load_t",
    "UnloadCostsPerTon_Euro": "unload_t",
}
"""BIVAS ``travel_cost_values`` column to the column of ``cost_rates.csv``."""

BIVAS_APPEARANCES: Mapping[str, str] = {
    "Overig": "overig",
    "Natte bulk": "natte_bulk",
    "Droge bulk": "droge_bulk",
    "Container": "container",
}
"""BIVAS ``appearance_types.Description`` to the appearance label used in cost rates."""

BIVAS_CEMT_COLUMNS: Mapping[str, str] = {
    "Id": "cemt_class_id",
    "Description": "label",
    "Width__m": "std_width_m",
    "CrossSection__m2": "std_cross_section_m2",
    "Depth__m": "std_depth_m",
    "MinimumAbsoluteUkc__m": "keel_clearance_abs_m",
    "MinimumRelativeUkc__m": "keel_clearance_rel",
    "Category": "waterway_type",
}
"""BIVAS ``cemt_class`` column to the column of ``bivas_cemt_classes.csv``."""

BIVAS_SHIP_TYPE_COLUMNS: Mapping[str, str] = {
    "ID": "ship_type_id",
    "Label": "rws_class",
    "Description": "description",
    "CEMTTypeID": "cemt_class_id",
    "Width_10%__m": "width_p10_m",
    "Width_50%__m": "width_p50_m",
    "Width_90%__m": "width_p90_m",
    "Length_10%__m": "length_p10_m",
    "Length_50%__m": "length_p50_m",
    "Length_90%__m": "length_p90_m",
    "EmptyDepth__m": "draught_empty_m",
}
"""BIVAS ``ship_types`` column to the name used when the ship-type sources are compared."""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise ValidationError(f"BIVAS database not found: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _query(db_path: str | Path, sql: str, parameters: tuple[int, ...] = ()) -> pd.DataFrame:
    connection = _connect(db_path)
    try:
        return pd.read_sql_query(sql, connection, params=list(parameters))
    except (sqlite3.Error, pd.errors.DatabaseError) as error:
        raise ValidationError(f"BIVAS database query failed: {error}") from error
    finally:
        connection.close()


def read_bivas_cost_sets(db_path: str | Path) -> dict[int, str]:
    """Return the cost sets of ``travel_costs`` as ``{Id: Description}``."""
    table = _query(db_path, "SELECT Id, Description FROM travel_costs ORDER BY Id")
    return {int(i): str(d) for i, d in zip(table["Id"], table["Description"], strict=True)}


def read_bivas_cost_rates(
    db_path: str | Path, cost_set: int = KOSTENKENGETALLEN_2021
) -> pd.DataFrame:
    """Return the cost rates of one cost set in the ``cost_rates.csv`` layout.

    One row per appearance and ship type, ordered by ``AppearanceTypeID`` then
    ``ShipTypeID``. ``price_level`` is the year in the cost set description.
    """
    sets = read_bivas_cost_sets(db_path)
    if cost_set not in sets:
        raise ValidationError(f"Cost set {cost_set} is not in travel_costs; found {sets}")
    table = _query(
        db_path,
        """
        SELECT a.Description AS appearance_label, v.ShipTypeID AS ship_type_id,
               s.Label AS rws_class, v.*
        FROM travel_cost_values AS v
        JOIN ship_types AS s ON s.ID = v.ShipTypeID
        JOIN appearance_types AS a ON a.ID = v.AppearanceTypeID
        WHERE v.TravelCosts = ?
        ORDER BY v.AppearanceTypeID, v.ShipTypeID
        """,
        (cost_set,),
    )
    if table.empty:
        raise ValidationError(f"travel_cost_values has no rows for cost set {cost_set}")
    unknown = set(table["appearance_label"]) - set(BIVAS_APPEARANCES)
    if unknown:
        raise ValidationError(f"Unknown BIVAS appearance types: {sorted(unknown)}")
    if table.duplicated(["appearance_label", "ship_type_id"]).any():
        raise ValidationError("travel_cost_values has duplicate appearance and ship type rows")
    description = sets[cost_set]
    digits = "".join(ch for ch in description if ch.isdigit())
    result = pd.DataFrame(
        {
            "appearance": table["appearance_label"].map(BIVAS_APPEARANCES),
            "ship_type_id": table["ship_type_id"].astype(int),
            "rws_class": table["rws_class"].astype(str),
        }
    )
    for source, target in BIVAS_COST_COLUMNS.items():
        result[target] = table[source].astype(float)
    result["price_level"] = int(digits) if digits else pd.NA
    result["source"] = f"BIVAS travel_cost_values, TravelCosts={cost_set} ({description})"
    return result


def read_bivas_cemt_classes(db_path: str | Path) -> pd.DataFrame:
    """Return ``cemt_class`` in the ``bivas_cemt_classes.csv`` layout, ordered by ``Id``."""
    columns = ", ".join(BIVAS_CEMT_COLUMNS)
    table = _query(db_path, f"SELECT {columns} FROM cemt_class ORDER BY Id")  # noqa: S608
    if table.empty:
        raise ValidationError("cemt_class has no rows")
    result = table.rename(columns=dict(BIVAS_CEMT_COLUMNS))
    result["waterway_type"] = result["waterway_type"].replace("", pd.NA)
    return result


def read_bivas_ship_types(db_path: str | Path) -> pd.DataFrame:
    """Return ``ship_types`` as ``ship_type_id`` and ``rws_class``, ordered by ``ID``."""
    table = _query(db_path, "SELECT ID, Label FROM ship_types ORDER BY ID")
    if table["Label"].duplicated().any():
        raise ValidationError("ship_types has duplicate labels")
    return table.rename(columns={"ID": "ship_type_id", "Label": "rws_class"})


def read_bivas_ship_type_table(db_path: str | Path) -> pd.DataFrame:
    """Return ``ship_types`` with its dimension columns and waterway class, ordered by ``ID``.

    Columns are renamed by :data:`BIVAS_SHIP_TYPE_COLUMNS`. ``cemt_class`` is the
    ``cemt_class.Description`` of ``CEMTTypeID``. Width and length are 10, 50 and 90 per cent
    percentiles. The empty draught is the only draught BIVAS holds.
    """
    columns = ", ".join(f's."{name}"' for name in BIVAS_SHIP_TYPE_COLUMNS)
    table = _query(
        db_path,
        f"""
        SELECT {columns}, c.Description AS cemt_class
        FROM ship_types AS s
        LEFT JOIN cemt_class AS c ON c.Id = s.CEMTTypeID
        ORDER BY s.ID
        """,  # noqa: S608
    )
    if table["Label"].duplicated().any():
        raise ValidationError("ship_types has duplicate labels")
    return table.rename(columns=dict(BIVAS_SHIP_TYPE_COLUMNS))
