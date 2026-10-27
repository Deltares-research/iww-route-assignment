"""Validated pandas adapters for external parameter tables."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..costs.fuel import TableFuelConsumptionProvider
from ..domain.classifications import normalise_cemt_class, normalise_rws_class
from ..domain.enums import FuelResolutionPolicy
from ..domain.models import (
    CostRates,
    FuelConsumptionObservation,
    FuelProxyWeights,
    LoadingAnchors,
    VesselReference,
)
from ..exceptions import ValidationError
from ._frames import number_value as _number
from ._frames import read_csv_table as _read_csv
from ._frames import require_columns as _require_columns
from ._frames import text_value as _text

_FUEL_COLUMNS = {
    "source_row_id",
    "vessel_rws_class",
    "waterway_cemt_class",
    "diesel_light_l_per_h",
    "diesel_medium_l_per_h",
    "diesel_heavy_l_per_h",
    "source_dataset",
}

_VESSEL_COLUMNS = {
    "rws_class",
    "cemt_class",
    "capacity_t",
    "beam_m",
    "length_m",
    "draught_loaded_m",
    "draught_empty_m",
}

_COST_COLUMNS = {
    "appearance",
    "rws_class",
    "labor_h",
    "labor_wait_h",
    "maint_h",
    "maint_km",
    "insurance_h",
    "interest_pct_h",
    "depreciation_h",
    "harbor_h",
    "other_h",
    "load_t",
    "unload_t",
    "source",
}


def load_fuel_observations_csv(path: str | Path) -> tuple[FuelConsumptionObservation, ...]:
    """Load the normalised, cargo-independent and index-free fuel contract."""
    table = _read_csv(path)
    _require_columns(table, _FUEL_COLUMNS, "fuel observations")
    obsolete = {"vessel_profile", "goods_type", "appearance"}.intersection(table.columns)
    if obsolete:
        raise ValidationError(
            f"Remove fuel goods/profile columns {sorted(obsolete)} by selecting a source dataset; "
            "do not merge conflicting rates"
        )
    keys = [
        (
            normalise_rws_class(_text(row, "vessel_rws_class")),
            normalise_cemt_class(_text(row, "waterway_cemt_class")),
        )
        for _, row in table.iterrows()
    ]
    if len(set(keys)) != len(keys):
        raise ValidationError("Duplicate fuel keys; select one unambiguous source dataset")
    return tuple(
        FuelConsumptionObservation(
            source_row_id=_text(row, "source_row_id"),
            vessel_rws_class=_text(row, "vessel_rws_class"),
            waterway_cemt_class=_text(row, "waterway_cemt_class"),
            diesel_light_l_per_h=_number(row, "diesel_light_l_per_h"),
            diesel_medium_l_per_h=_number(row, "diesel_medium_l_per_h"),
            diesel_heavy_l_per_h=_number(row, "diesel_heavy_l_per_h"),
            source_dataset=_text(row, "source_dataset"),
        )
        for _, row in table.iterrows()
    )


def load_vessel_references_csv(path: str | Path) -> tuple[VesselReference, ...]:
    """Load vessel attributes used by the audited nearest-vessel policy."""
    table = _read_csv(path)
    _require_columns(table, _VESSEL_COLUMNS, "vessel references")
    return tuple(
        VesselReference(
            vessel_rws_class=_text(row, "rws_class"),
            vessel_cemt_class=_text(row, "cemt_class"),
            capacity_t=_number(row, "capacity_t"),
            beam_m=_number(row, "beam_m"),
            length_m=_number(row, "length_m"),
            draught_loaded_m=_number(row, "draught_loaded_m"),
            draught_empty_m=_number(row, "draught_empty_m"),
        )
        for _, row in table.iterrows()
    )


def load_cost_rates_csv(path: str | Path) -> tuple[CostRates, ...]:
    """Load the BIVAS-derived travel-cost schema used by the external data folder."""
    table = _read_csv(path)
    _require_columns(table, _COST_COLUMNS, "cost rates")
    result = tuple(
        CostRates(
            appearance=_text(row, "appearance"),
            vessel_rws_class=_text(row, "rws_class"),
            labour_eur_per_h=_number(row, "labor_h"),
            labour_waiting_eur_per_h=_number(row, "labor_wait_h"),
            maintenance_eur_per_h=_number(row, "maint_h"),
            maintenance_eur_per_km=_number(row, "maint_km"),
            insurance_eur_per_h=_number(row, "insurance_h"),
            interest_eur_per_percent_h=_number(row, "interest_pct_h"),
            depreciation_eur_per_h=_number(row, "depreciation_h"),
            port_dues_eur_per_h=_number(row, "harbor_h"),
            other_eur_per_h=_number(row, "other_h"),
            loading_eur_per_t=_number(row, "load_t"),
            unloading_eur_per_t=_number(row, "unload_t"),
            source=_text(row, "source"),
        )
        for _, row in table.iterrows()
    )
    _reject_duplicate_cost_keys(result)
    return result


def select_cost_rates(
    rates: Iterable[CostRates], appearance: str, vessel_rws_class: str
) -> CostRates:
    """Select one unique cost-rate row using case-insensitive appearance matching."""
    appearance_key = appearance.strip().casefold()
    vessel_key = normalise_rws_class(vessel_rws_class).casefold()
    matches = [
        row
        for row in rates
        if row.appearance.casefold() == appearance_key
        and row.vessel_rws_class.casefold() == vessel_key
    ]
    if len(matches) != 1:
        raise ValidationError(
            "Expected one cost-rate row for "
            f"appearance={appearance!r}, vessel_rws_class={vessel_rws_class!r}; "
            f"found {len(matches)}"
        )
    return matches[0]


def load_fuel_provider(
    fuel_path: str | Path,
    vessel_reference_path: str | Path,
    *,
    policy: FuelResolutionPolicy = FuelResolutionPolicy.AUDITED_PROXY,
    loading_anchors: LoadingAnchors | None = None,
    proxy_weights: FuelProxyWeights | None = None,
) -> TableFuelConsumptionProvider:
    """Build the standard table provider from validated external CSV adapters."""
    return TableFuelConsumptionProvider(
        load_fuel_observations_csv(fuel_path),
        load_vessel_references_csv(vessel_reference_path),
        policy=policy,
        loading_anchors=loading_anchors or LoadingAnchors(),
        proxy_weights=proxy_weights or FuelProxyWeights(),
    )


def _reject_duplicate_cost_keys(rates: tuple[CostRates, ...]) -> None:
    keys: set[tuple[str, str]] = set()
    for row in rates:
        key = (row.appearance.casefold(), row.vessel_rws_class.casefold())
        if key in keys:
            raise ValidationError(
                "Duplicate cost-rate key for "
                f"appearance={row.appearance!r}, vessel_rws_class={row.vessel_rws_class!r}"
            )
        keys.add(key)
