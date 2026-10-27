"""Shared pandas table helpers used by the file adapters."""

from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

import pandas as pd

from ..exceptions import ValidationError


def read_csv_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV file and reject generated index columns and empty tables."""
    source = Path(path)
    if not source.is_file():
        raise ValidationError(f"CSV file does not exist: {source}")
    try:
        table = pd.read_csv(source, dtype="string")
    except Exception as error:
        raise ValidationError(f"Could not read CSV file {source}: {error}") from error
    generated_index_columns = [
        str(column) for column in table.columns if str(column).startswith("Unnamed:")
    ]
    if generated_index_columns:
        raise ValidationError(
            f"CSV file {source} contains generated index columns {generated_index_columns}; "
            "write normalised files with index=False"
        )
    if table.empty:
        raise ValidationError(f"CSV file contains no data rows: {source}")
    return table


def require_columns(table: pd.DataFrame, required: set[str], table_name: str) -> None:
    """Raise when a table is missing any required column."""
    missing = sorted(required.difference(str(column) for column in table.columns))
    if missing:
        raise ValidationError(f"{table_name} is missing required columns: {', '.join(missing)}")


def text_value(row: pd.Series, column: str) -> str:
    """Return a required text cell."""
    value = row[column]
    if pd.isna(value):
        raise ValidationError(f"Column {column!r} contains a missing value")
    return str(value)


def optional_text_value(row: pd.Series, column: str, default: str = "") -> str:
    """Return a text cell, or a default when the column or value is missing."""
    if column not in row.index:
        return default
    value = row[column]
    if pd.isna(value):
        return default
    return str(value)


def number_value(row: pd.Series, column: str) -> float:
    """Return a required numeric cell."""
    value = row[column]
    if pd.isna(value):
        raise ValidationError(f"Column {column!r} contains a missing value")
    return _to_float(value, column)


def optional_number_value(row: pd.Series, column: str, default: float = 0.0) -> float:
    """Return a numeric cell, or a default when the column or value is missing."""
    if column not in row.index:
        return default
    value = row[column]
    if pd.isna(value):
        return default
    return _to_float(value, column)


def integer_value(row: pd.Series, column: str) -> int:
    """Return a required integer cell, rejecting non-integral numbers."""
    value = number_value(row, column)
    if value != int(value):
        raise ValidationError(f"Column {column!r} contains a non-integer value {value!r}")
    return int(value)


def _to_float(value: Any, column: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"Column {column!r} contains a boolean, not a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            f"Column {column!r} contains a non-numeric value {value!r}"
        ) from error
    if not isfinite(number):
        raise ValidationError(f"Column {column!r} must be finite")
    return number
