"""Build the reference tables the model reads, from their original sources.

Two steps, each one function:

1. :func:`extract_parameter_tables` reads the original sources and writes four parameter
   tables: cost rates and waterway classes from the BIVAS database, vessel dimensions from the
   DTV ship-type file, and energy use from the CE Delft figures. It also writes a comparison
   of the two ship-type sources, BIVAS ``ship_types`` and DTV.
2. :func:`build_shared_inputs` turns those parameter tables into the files the cost function,
   the navigability check and the assignment load.

Every rule that fills, drops or converts a value has a label, written to ``build_report.json``.
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.classifications import CEMT_ORDER, normalise_rws_class
from ..exceptions import ValidationError
from .bivas import (
    KOSTENKENGETALLEN_2021,
    read_bivas_cemt_classes,
    read_bivas_cost_rates,
    read_bivas_ship_type_table,
    read_bivas_ship_types,
)
from .fuel_preparation import DIESEL_ENERGY_MJ_PER_L
from .provenance import run_metadata, write_json

COST_RATES_FILE = "cost_rates_2021.csv"
CEMT_CLASSES_FILE = "bivas_cemt_classes.csv"
VESSEL_TABLE_FILE = "rws_to_cemt.csv"
ENERGY_USE_FILE = "ce_delft_energy_use.csv"
SHIP_TYPE_COMPARISON_FILE = "ship_type_comparison.csv"

DTV_SOURCE = "DTV shiptypes database (digitaltwin-waterway); CEMT normalised per fis-crawler rules"
ENERGY_SOURCE = "CE Delft STREAM energy use per vessel and waterway class, megajoules per km"

DTV_COLUMNS: Mapping[str, str] = {
    "Load weight maximum [ton]": "capacity_t",
    "Beam [m]": "beam_m",
    "Length [m]": "length_m",
    "Draught loaded [m]": "draught_loaded_m",
    "Draught empty [m]": "draught_empty_m",
    "Height average [m]": "height_m",
}
"""DTV ship-type field to the column of ``rws_to_cemt.csv``."""

DTV_CEMT_ALIASES: Mapping[str, str] = {"IVa": "IV", "IVb": "IV", "VIIa": "VIc"}
"""DTV waterway class labels folded into the classes the model supports."""

SHIP_TYPE_SHARED_FIELDS: Mapping[str, str] = {
    "width_p10_m": "Beam 10% percentile [m]",
    "width_p50_m": "Beam 50% percentile [m]",
    "width_p90_m": "Beam 90% percentile [m]",
    "length_p10_m": "Length 10% percentile [m]",
    "length_p50_m": "Length 50% percentile [m]",
    "length_p90_m": "Length 90% percentile [m]",
    "draught_empty_m": "Draught empty [m]",
}
"""Value held by both ship-type sources: renamed BIVAS ``ship_types`` column to DTV field."""

ENERGY_PROFILES: Mapping[str, str] = {
    "vessel_data": "general",
    "container_vessel_data": "container",
}
"""Notebook variable to the cargo profile of that CE Delft table."""

FUEL_SOURCE_RULE = "assumption:container-source-for-classes-absent-from-general-v1"
AIR_DRAUGHT_RULE = "assumption:height_m-is-air-draught-for-both-loading-states-v1"
ENERGY_UNIT_RULE = f"conversion:MJ-per-km-divided-by-{DIESEL_ENERGY_MJ_PER_L:g}-MJ-per-litre"

VESSEL_REFERENCE_COLUMNS = [
    "rws_class",
    "cemt_class",
    "capacity_t",
    "beam_m",
    "length_m",
    "draught_loaded_m",
    "draught_empty_m",
]


@dataclass(frozen=True, slots=True)
class ParameterSources:
    """The original sources of the parameter tables."""

    bivas_db: Path
    dtv_ship_types: Path
    energy_notebook: Path


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _read_dtv_records(dtv_json: str | Path) -> dict[str, dict[str, Any]]:
    """Return the DTV records by lower-case ``RWS-class``. A repeated class stops the read."""
    path = Path(dtv_json)
    if not path.is_file():
        raise ValidationError(f"DTV ship-type file not found: {path}")
    records = [r for r in json.loads(path.read_text(encoding="utf-8")) if r.get("RWS-class")]
    by_label = {str(r["RWS-class"]).casefold(): r for r in records}
    if len(by_label) != len(records):
        raise ValidationError("The DTV ship-type file repeats an RWS-class")
    return by_label


def read_dtv_vessel_table(dtv_json: str | Path, ship_types: pd.DataFrame) -> pd.DataFrame:
    """Join the BIVAS ship types to the DTV ship-type file, in ``rws_to_cemt.csv`` layout.

    Labels are matched without regard to letter case, because BIVAS writes ``BII-2B`` where
    DTV writes ``BII-2b``. A BIVAS ship type missing from DTV stops the build. A DTV class
    missing from BIVAS is left out, because it has no BIVAS cost rates.
    """
    by_label = _read_dtv_records(dtv_json)
    rows = []
    ship: Any
    for ship in ship_types.itertuples(index=False):
        record = by_label.get(str(ship.rws_class).casefold())
        if record is None:
            raise ValidationError(f"Ship type {ship.rws_class!r} is not in the DTV file")
        raw = str(record["CEMT-class"])
        row: dict[str, object] = {
            "ship_type_id": int(ship.ship_type_id),
            "rws_class": ship.rws_class,
            "cemt_class_raw": raw,
            "cemt_class": DTV_CEMT_ALIASES.get(raw, raw),
        }
        row |= {target: record.get(source) for source, target in DTV_COLUMNS.items()}
        row["source"] = DTV_SOURCE
        rows.append(row)
    return pd.DataFrame(rows)


def _label(value: Any) -> str | None:
    return None if value is None or pd.isna(value) else str(value).strip()


def _differs(left: Any, right: Any) -> bool:
    """True only when both values are present and differ by more than 1e-9."""
    if left is None or right is None or pd.isna(left) or pd.isna(right):
        return False
    return not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)


def compare_ship_type_sources(bivas_ship_types: pd.DataFrame, dtv_json: str | Path) -> pd.DataFrame:
    """Compare BIVAS ``ship_types`` with the DTV ship-type file, one row per class in either.

    ``bivas_ship_types`` is the table of ``read_bivas_ship_type_table``. Classes are matched
    without regard to letter case. Per class the result gives:

    - ``in_bivas``, ``in_dtv``: where the class occurs;
    - ``bivas_cemt_class``, ``dtv_cemt_class`` and ``cemt_agrees``, after folding the DTV label
      with :data:`DTV_CEMT_ALIASES`;
    - ``shared_values_agree`` and ``differing_values``: the values both sources hold
      (:data:`SHIP_TYPE_SHARED_FIELDS`). A value missing on either side is not compared;
    - the class beam and length the model takes from DTV, beside the BIVAS medians.

    BIVAS classes come first in ``ship_type_id`` order, then DTV-only classes by label.
    """
    by_label = _read_dtv_records(dtv_json)
    rows: list[dict[str, object]] = []
    ordered = bivas_ship_types.sort_values("ship_type_id", kind="stable")
    for record in ordered.to_dict("records"):
        ship = {str(key): value for key, value in record.items()}
        rows.append(_comparison_row(ship, by_label.pop(str(ship["rws_class"]).casefold(), None)))
    for key in sorted(by_label):
        rows.append(_comparison_row(None, by_label[key]))
    table = pd.DataFrame(rows)
    table["ship_type_id"] = table["ship_type_id"].astype("Int64")
    for column in ("cemt_agrees", "shared_values_agree"):
        table[column] = table[column].astype("boolean")
    return table


def _comparison_row(
    ship: Mapping[str, Any] | None, record: Mapping[str, Any] | None
) -> dict[str, object]:
    bivas: Mapping[str, Any] = ship or {}
    dtv: Mapping[str, Any] = record or {}
    both = ship is not None and record is not None
    bivas_cemt = _label(bivas.get("cemt_class"))
    dtv_cemt = _label(dtv.get("CEMT-class"))
    folded = None if dtv_cemt is None else DTV_CEMT_ALIASES.get(dtv_cemt, dtv_cemt)
    differing = [
        column
        for column, field in SHIP_TYPE_SHARED_FIELDS.items()
        if both and _differs(bivas.get(column), dtv.get(field))
    ]
    return {
        "rws_class": bivas.get("rws_class", dtv.get("RWS-class")),
        "ship_type_id": bivas.get("ship_type_id"),
        "in_bivas": ship is not None,
        "in_dtv": record is not None,
        "bivas_cemt_class": bivas_cemt,
        "dtv_cemt_class": dtv_cemt,
        "cemt_agrees": (folded == bivas_cemt) if both else None,
        "shared_values_agree": (not differing) if both else None,
        "differing_values": ";".join(differing),
        "dtv_beam_m": dtv.get("Beam [m]"),
        "bivas_width_p50_m": bivas.get("width_p50_m"),
        "dtv_length_m": dtv.get("Length [m]"),
        "bivas_length_p50_m": bivas.get("length_p50_m"),
    }


def read_energy_use_from_notebook(notebook: str | Path) -> pd.DataFrame:
    """Read the two CE Delft energy tables entered in the fuel notebook.

    The notebook holds them as literal lists named ``vessel_data`` (general cargo) and
    ``container_vessel_data``. Values are megajoules per km at light, medium and heavy load.
    """
    path = Path(notebook)
    if not path.is_file():
        raise ValidationError(f"Fuel notebook not found: {path}")
    cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
    found: dict[str, list[dict[str, object]]] = {}
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        try:
            tree = ast.parse("".join(cell["source"]))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in ENERGY_PROFILES:
                    found[name] = ast.literal_eval(node.value)
    missing = set(ENERGY_PROFILES) - set(found)
    if missing:
        raise ValidationError(f"Fuel notebook has no list named {sorted(missing)}")
    frames = []
    for name, profile in ENERGY_PROFILES.items():
        frame = pd.DataFrame(found[name])
        frames.append(
            pd.DataFrame(
                {
                    "cargo_profile": profile,
                    "vessel_category": frame["vessel_category"],
                    "vessel_rws_class": frame["vessel_rws_class"].map(normalise_rws_class),
                    "waterway_cemt_class": frame["waterway_cemt_class"].astype(str),
                    "energy_light_mj_per_km": frame["diesel_light"].astype(float),
                    "energy_medium_mj_per_km": frame["diesel_medium"].astype(float),
                    "energy_heavy_mj_per_km": frame["diesel_heavy"].astype(float),
                    "source": ENERGY_SOURCE,
                }
            )
        )
    table = pd.concat(frames, ignore_index=True)
    keys = ["cargo_profile", "vessel_rws_class", "waterway_cemt_class"]
    if table.duplicated(keys).any():
        raise ValidationError("The fuel notebook repeats a vessel and waterway within one table")
    return table


def extract_parameter_tables(
    sources: ParameterSources,
    output_dir: str | Path,
    *,
    cost_set: int = KOSTENKENGETALLEN_2021,
) -> dict[str, Path]:
    """Write the four parameter tables from their original sources.

    Also writes ``ship_type_comparison.csv``, the comparison of BIVAS ``ship_types`` with the
    DTV file. Returns the written paths by name. A ``build_report.json`` records the checksums
    of the sources, the cost set used, and where the two ship-type sources differ.
    """
    folder = Path(output_dir)
    ship_types = read_bivas_ship_types(sources.bivas_db)
    comparison = compare_ship_type_sources(
        read_bivas_ship_type_table(sources.bivas_db), sources.dtv_ship_types
    )
    tables = {
        COST_RATES_FILE: read_bivas_cost_rates(sources.bivas_db, cost_set),
        CEMT_CLASSES_FILE: read_bivas_cemt_classes(sources.bivas_db),
        VESSEL_TABLE_FILE: read_dtv_vessel_table(sources.dtv_ship_types, ship_types),
        ENERGY_USE_FILE: read_energy_use_from_notebook(sources.energy_notebook),
        SHIP_TYPE_COMPARISON_FILE: comparison,
    }
    paths = {name: _write_csv(table, folder / name) for name, table in tables.items()}
    cemt_differs = comparison[comparison["cemt_agrees"].eq(False).fillna(False)]
    values_differ = comparison[comparison["differing_values"] != ""]
    write_json(
        folder / "build_report.json",
        run_metadata(
            {
                "bivas_db": sources.bivas_db,
                "dtv_ship_types": sources.dtv_ship_types,
                "energy_notebook": sources.energy_notebook,
            },
            bivas_cost_set=cost_set,
            bivas_tables=[
                "travel_cost_values",
                "travel_costs",
                "ship_types",
                "appearance_types",
                "cemt_class",
            ],
            rows={name: len(table) for name, table in tables.items()},
            ship_types_only_in_dtv=sorted(comparison.loc[~comparison["in_bivas"], "rws_class"]),
            ship_types_only_in_bivas=sorted(comparison.loc[~comparison["in_dtv"], "rws_class"]),
            cemt_class_differences={
                str(row.rws_class): {"bivas": row.bivas_cemt_class, "dtv": row.dtv_cemt_class}
                for row in cemt_differs.itertuples(index=False)
            },
            ship_type_value_differences={
                str(row.rws_class): str(row.differing_values)
                for row in values_differ.itertuples(index=False)
            },
        ),
    )
    return paths


def fuel_observations_from_energy(
    energy: pd.DataFrame, *, diesel_energy_mj_per_l: float = DIESEL_ENERGY_MJ_PER_L
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Turn CE Delft energy use into fuel observations in litres per km.

    The general-cargo table is the selected source. A vessel class that the general table
    does not have at all takes its rows from the container table. A class present in both
    uses the general rows only, so no vessel and waterway key is ever merged. Returns the
    observations and the classes taken from each table.
    """
    if diesel_energy_mj_per_l <= 0:
        raise ValidationError("diesel_energy_mj_per_l must be positive")
    general = energy[energy["cargo_profile"] == "general"]
    container = energy[energy["cargo_profile"] == "container"]
    if general.empty:
        raise ValidationError("The energy table has no general-cargo rows")
    extra = container[~container["vessel_rws_class"].isin(set(general["vessel_rws_class"]))]
    chosen = pd.concat([general, extra], ignore_index=True)
    unsupported = sorted(set(chosen["waterway_cemt_class"]) - set(CEMT_ORDER))
    if unsupported:
        raise ValidationError(f"Unsupported waterway classes in the energy table: {unsupported}")
    if chosen.duplicated(["vessel_rws_class", "waterway_cemt_class"]).any():
        raise ValidationError("Duplicate fuel keys after source selection")
    chosen = chosen.sort_values(["vessel_rws_class", "waterway_cemt_class"], kind="stable")
    label = chosen["source"] + "; " + chosen["cargo_profile"] + " table; " + ENERGY_UNIT_RULE
    observations = pd.DataFrame(
        {
            "source_row_id": "ce_delft:"
            + chosen["cargo_profile"]
            + ":"
            + chosen["vessel_rws_class"]
            + ":"
            + chosen["waterway_cemt_class"],
            "vessel_rws_class": chosen["vessel_rws_class"],
            "waterway_cemt_class": chosen["waterway_cemt_class"],
            "diesel_light_l_per_km": chosen["energy_light_mj_per_km"] / diesel_energy_mj_per_l,
            "diesel_medium_l_per_km": chosen["energy_medium_mj_per_km"] / diesel_energy_mj_per_l,
            "diesel_heavy_l_per_km": chosen["energy_heavy_mj_per_km"] / diesel_energy_mj_per_l,
            "source_dataset": label,
        }
    ).reset_index(drop=True)
    classes = {
        "general": sorted(set(general["vessel_rws_class"])),
        "container": sorted(set(extra["vessel_rws_class"])),
    }
    return observations, classes


def vessel_tables(vessels: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Return vessel references, navigability vessel states and the dropped classes.

    A class is kept as a reference only when every dimension is present and positive and its
    waterway class is supported. Nothing is filled in. Each kept or partly known class gives a
    loaded and an empty navigability state, with the average height as air draught.
    """
    table = vessels.copy()
    table["cemt_class"] = table["cemt_class"].astype(str).str.strip()
    numeric = VESSEL_REFERENCE_COLUMNS[2:]
    keep = table[VESSEL_REFERENCE_COLUMNS].notna().all(axis=1)
    keep &= (table[numeric] > 0).all(axis=1) & table["cemt_class"].isin(CEMT_ORDER)
    states = []
    ship: Any
    for ship in table.itertuples(index=False):
        for state, draught in (("loaded", ship.draught_loaded_m), ("empty", ship.draught_empty_m)):
            values = (ship.length_m, ship.beam_m, draught)
            if any(pd.isna(value) or value <= 0 for value in values):
                continue
            states.append(
                {
                    "vessel_id": f"{ship.rws_class}-{state}",
                    "length_m": ship.length_m,
                    "beam_m": ship.beam_m,
                    "draught_m": draught,
                    "air_draught_m": ship.height_m,
                    "carries_dangerous_goods": False,
                    "source": f"{VESSEL_TABLE_FILE}; {AIR_DRAUGHT_RULE}",
                }
            )
    dropped = sorted(table.loc[~keep, "rws_class"].astype(str))
    return table.loc[keep, VESSEL_REFERENCE_COLUMNS], pd.DataFrame(states), dropped


def build_shared_inputs(parameters_dir: str | Path, output_dir: str | Path) -> dict[str, Path]:
    """Write the files the model loads, from the parameter tables.

    Reads ``cost_rates_2021.csv``, ``rws_to_cemt.csv`` and ``ce_delft_energy_use.csv`` from
    ``parameters_dir``. Writes ``cost_rates.csv``, ``fuel_observations.csv``,
    ``vessel_references.csv``, ``navigability_vessels.csv`` and ``build_report.json`` to
    ``output_dir``. Refuses to write into a folder that already holds files.
    """
    source = Path(parameters_dir)
    target = Path(output_dir)
    inputs = {name: source / name for name in (COST_RATES_FILE, VESSEL_TABLE_FILE, ENERGY_USE_FILE)}
    for path in inputs.values():
        if not path.is_file():
            raise ValidationError(f"Parameter table not found: {path}")
    if target.exists() and any(target.iterdir()):
        raise ValidationError(f"{target} already holds files; choose a new folder")

    fuel, fuel_classes = fuel_observations_from_energy(pd.read_csv(inputs[ENERGY_USE_FILE]))
    references, states, dropped = vessel_tables(pd.read_csv(inputs[VESSEL_TABLE_FILE]))
    paths = {
        "cost_rates": _write_csv(pd.read_csv(inputs[COST_RATES_FILE]), target / "cost_rates.csv"),
        "fuel_observations": _write_csv(fuel, target / "fuel_observations.csv"),
        "vessel_references": _write_csv(references, target / "vessel_references.csv"),
        "navigability_vessels": _write_csv(states, target / "navigability_vessels.csv"),
    }
    paths["build_report"] = write_json(
        target / "build_report.json",
        run_metadata(
            inputs,
            rules=[
                ENERGY_UNIT_RULE,
                FUEL_SOURCE_RULE,
                AIR_DRAUGHT_RULE,
                "fuel key is vessel and waterway only; no goods profile",
            ],
            fuel_rows=len(fuel),
            fuel_classes_from_general_table=fuel_classes["general"],
            fuel_classes_from_container_table=fuel_classes["container"],
            vessel_reference_rows=len(references),
            vessel_classes_dropped_for_missing_values=dropped,
            navigability_vessel_states=len(states),
        ),
    )
    return paths
