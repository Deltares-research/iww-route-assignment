"""Prepare cargo-independent fuel observations from an explicitly selected source.

The source is the CE Delft STREAM table of energy use per vessel class and waterway class
at light, medium and heavy load. Its values are megajoules per kilometre. The library
works in litres per kilometre, so the unit of the source must be stated on every call.
"""

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

DIESEL_ENERGY_MJ_PER_L = 36.0
"""Lower heating value of diesel used to turn megajoules into litres."""

VALUE_UNITS = ("MJ_per_km", "l_per_km")

_OUTPUT_COLUMNS = [
    "source_row_id",
    "vessel_rws_class",
    "waterway_cemt_class",
    "diesel_light_l_per_km",
    "diesel_medium_l_per_km",
    "diesel_heavy_l_per_km",
    "source_dataset",
]


def prepare_notebook_fuel_observations(
    general: pd.DataFrame,
    container: pd.DataFrame,
    *,
    selected_source: str,
    value_unit: str,
    diesel_energy_mj_per_l: float = DIESEL_ENERGY_MJ_PER_L,
    general_source: str = "notebook:general",
    container_source: str = "notebook:container",
) -> pd.DataFrame:
    """Select one of the two notebook datasets without filling or merging rows.

    ``value_unit`` states the unit of ``diesel_light``, ``diesel_medium`` and
    ``diesel_heavy``: ``MJ_per_km`` (the CE Delft source) or ``l_per_km``. Megajoules are
    divided by ``diesel_energy_mj_per_l`` and the conversion is recorded in
    ``source_dataset``.

    Waterway interpolation is performed by the fuel provider at lookup time. This
    preserves both source rows and the interpolation fraction in result metadata.
    """
    if selected_source not in {"general", "container"}:
        raise ValidationError("selected_source must explicitly be general or container")
    if value_unit not in VALUE_UNITS:
        raise ValidationError(f"value_unit must be one of {VALUE_UNITS}; received {value_unit!r}")
    if not isfinite(diesel_energy_mj_per_l) or diesel_energy_mj_per_l <= 0:
        raise ValidationError("diesel_energy_mj_per_l must be positive")
    divisor = diesel_energy_mj_per_l if value_unit == "MJ_per_km" else 1.0
    table, source = (
        (general, general_source) if selected_source == "general" else (container, container_source)
    )
    result = _normalise_source(table, source, divisor)
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
    value_unit: str,
    diesel_energy_mj_per_l: float = DIESEL_ENERGY_MJ_PER_L,
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
        value_unit=value_unit,
        diesel_energy_mj_per_l=diesel_energy_mj_per_l,
        general_source=general_source,
        container_source=container_source,
    )
    table.to_csv(output, index=False)
    return output


def _normalise_source(table: pd.DataFrame, source_dataset: str, divisor: float) -> pd.DataFrame:
    missing = sorted(_RAW_COLUMNS.difference(str(column) for column in table.columns))
    if missing:
        raise ValidationError(f"Notebook fuel table is missing columns: {', '.join(missing)}")
    if not source_dataset.strip():
        raise ValidationError("Fuel source dataset must not be empty")

    label = source_dataset.strip()
    if divisor != 1.0:
        label = f"{label}; MJ per km divided by {divisor:g} MJ per litre"
    records: list[dict[str, object]] = []
    for _, row in table.iterrows():
        vessel = normalise_rws_class(_required_text(row, "vessel_rws_class"))
        waterway = normalise_cemt_class(_required_text(row, "waterway_cemt_class"))
        records.append(
            {
                "source_row_id": f"{source_dataset}:{vessel}:{waterway}",
                "vessel_rws_class": vessel,
                "waterway_cemt_class": waterway,
                "diesel_light_l_per_km": _required_number(row, "diesel_light") / divisor,
                "diesel_medium_l_per_km": _required_number(row, "diesel_medium") / divisor,
                "diesel_heavy_l_per_km": _required_number(row, "diesel_heavy") / divisor,
                "source_dataset": label,
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
