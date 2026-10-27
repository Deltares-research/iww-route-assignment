"""Prepare cargo-independent fuel observations from an explicitly selected source."""

from __future__ import annotations

from math import isfinite
from pathlib import Path

import pandas as pd

from ..domain.classifications import normalise_cemt_class, normalise_rws_class
from ..exceptions import DuplicateFuelObservationError, ValidationError

_RAW_COLUMNS = {
    "vessel_rws_class",
    "waterway_cemt_class",
    "diesel_light",
    "diesel_medium",
    "diesel_heavy",
}

_OUTPUT_COLUMNS = [
    "source_row_id",
    "vessel_rws_class",
    "waterway_cemt_class",
    "diesel_light_l_per_h",
    "diesel_medium_l_per_h",
    "diesel_heavy_l_per_h",
    "source_dataset",
]


def prepare_notebook_fuel_observations(
    general: pd.DataFrame,
    container: pd.DataFrame,
    *,
    selected_source: str,
    general_source: str = "notebook:general",
    container_source: str = "notebook:container",
) -> pd.DataFrame:
    """Select one of the two notebook datasets without filling or merging rows.

    Waterway interpolation is performed by the fuel provider at lookup time. This
    preserves both source rows and the interpolation fraction in result metadata.
    """
    if selected_source not in {"general", "container"}:
        raise ValidationError("selected_source must explicitly be general or container")
    table, source = (
        (general, general_source) if selected_source == "general" else (container, container_source)
    )
    result = _normalise_source(table, source)
    duplicate = result.duplicated(["vessel_rws_class", "waterway_cemt_class"], keep=False)
    if duplicate.any():
        keys = result.loc[
            duplicate,
            ["vessel_rws_class", "waterway_cemt_class"],
        ].drop_duplicates()
        raise DuplicateFuelObservationError(
            "Duplicate notebook fuel keys remain within the selected source: "
            f"{keys.to_dict(orient='records')}"
        )
    return result.sort_values(
        ["vessel_rws_class", "waterway_cemt_class", "source_row_id"]
    ).reset_index(drop=True)


def write_notebook_fuel_observations_csv(
    general: pd.DataFrame,
    container: pd.DataFrame,
    output_path: str | Path,
    *,
    selected_source: str,
    general_source: str = "notebook:general",
    container_source: str = "notebook:container",
) -> Path:
    """Write the normalised contract without a generated CSV index column."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    table = prepare_notebook_fuel_observations(
        general,
        container,
        selected_source=selected_source,
        general_source=general_source,
        container_source=container_source,
    )
    table.to_csv(output, index=False)
    return output


def _normalise_source(table: pd.DataFrame, source_dataset: str) -> pd.DataFrame:
    missing = sorted(_RAW_COLUMNS.difference(str(column) for column in table.columns))
    if missing:
        raise ValidationError(f"Notebook fuel table is missing columns: {', '.join(missing)}")
    if not source_dataset.strip():
        raise ValidationError("Fuel source dataset must not be empty")

    records: list[dict[str, object]] = []
    for _, row in table.iterrows():
        vessel = normalise_rws_class(_required_text(row, "vessel_rws_class"))
        waterway = normalise_cemt_class(_required_text(row, "waterway_cemt_class"))
        records.append(
            {
                "source_row_id": f"{source_dataset}:{vessel}:{waterway}",
                "vessel_rws_class": vessel,
                "waterway_cemt_class": waterway,
                "diesel_light_l_per_h": _required_number(row, "diesel_light"),
                "diesel_medium_l_per_h": _required_number(row, "diesel_medium"),
                "diesel_heavy_l_per_h": _required_number(row, "diesel_heavy"),
                "source_dataset": source_dataset.strip(),
            }
        )
    if not records:
        raise ValidationError("Notebook fuel table contains no rows")
    return pd.DataFrame.from_records(records, columns=_OUTPUT_COLUMNS)


def _required_text(row: pd.Series, column: str) -> str:
    value = row[column]
    if pd.isna(value) or not str(value).strip():
        raise ValidationError(f"Notebook fuel column {column!r} contains an empty value")
    return str(value)


def _required_number(row: pd.Series, column: str) -> float:
    value = row[column]
    if pd.isna(value):
        raise ValidationError(f"Notebook fuel column {column!r} contains a missing value")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            f"Notebook fuel column {column!r} contains a non-numeric value {value!r}"
        ) from error
    if not isfinite(number) or number < 0:
        raise ValidationError(
            f"Notebook fuel column {column!r} must be finite and non-negative; received {number!r}"
        )
    return number
