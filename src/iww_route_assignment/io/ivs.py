"""Adapters for the IVS trip register and the route-profile companion table.

The IVS weekly monitor export is a trip-level origin-destination register. It
carries the vessel type, the cargo weight and the container count, but neither a
sailing distance nor a waterway class. Those two fields come from a separate
route-profile table keyed on the origin and destination UN/LOCODE pair.

Parquet is deliberately not read here. The package depends on pandas only, so a
caller that holds a parquet reader passes a data frame to
:func:`ivs_trips_from_frame`. That keeps the optional binary dependency in the
run environment rather than in the library.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import (
    AppearanceRule,
    ArcCostRequest,
    RouteProfile,
    TripObservation,
    TripRejection,
)
from ..exceptions import ValidationError
from ._frames import (
    integer_value,
    number_value,
    optional_number_value,
    optional_text_value,
    read_csv_table,
    require_columns,
    text_value,
)

IVS_COLUMNS: Mapping[str, str] = {
    "unlo_herkomst": "origin_locode",
    "unlo_bestemming": "destination_locode",
    "v15_1_scheepstype_rws": "vessel_type_id",
    "v18_laadvermogen": "capacity_t",
    "v38_vervoerd_gewicht": "cargo_t",
    "v30_4_containers_teu_s": "containers_teu",
    "nstr_nw": "cargo_code",
}
"""Required IVS export columns and the trip field each one supplies."""

IVS_OPTIONAL_COLUMNS: Mapping[str, str] = {
    "v28_beladingscode": "loading_code",
    "v05_06_begindt_evenement_iso": "observed_on",
}
"""IVS columns retained for auditing but not required by the derivation."""

CARGO_WEIGHT_KG_TO_T = 0.001
"""Unit conversion for ``v38_vervoerd_gewicht``.

The export records the transported weight in kilogrammes while
``v18_laadvermogen`` records capacity in tonnes. The profile of the November
2023 weekly monitor shows a mean transported weight of about 1.13 million and a
mean capacity of about 2 527, which is consistent with kilogrammes against
tonnes. Both fields are converted to tonnes here so the loading ratio is
dimensionless.
"""

IVS_EMPTY_LOADING_CODE = "1"
"""Value of ``v28_beladingscode`` that marks a trip as sailing empty.

In the profiled export the count of this code equals the number of missing
transported weights exactly, so a missing weight on such a row means zero cargo
rather than an unknown quantity. A missing weight on any other code stays
unknown and is rejected with a reason code.
"""

ROUTE_PROFILE_COLUMNS = {
    "origin_locode",
    "destination_locode",
    "distance_km",
    "waterway_cemt_class",
    "realised_speed_km_per_h",
    "source",
}

VESSEL_TYPE_COLUMNS = {"ship_type_id", "rws_class"}

APPEARANCE_MAP_COLUMNS = {"nstr_chapter", "appearance", "source"}

ARC_REQUEST_COLUMNS: tuple[str, ...] = (
    "case_row_id",
    "arc_id",
    "appearance",
    "vessel_rws_class",
    "waterway_cemt_class",
    "loading_ratio",
    "distance_km",
    "realised_speed_km_per_h",
    "cargo_t",
    "is_loaded",
)
"""Columns the generic case runner requires, in a stable order."""


def ivs_trips_from_frame(
    frame: pd.DataFrame, *, source_dataset: str
) -> tuple[TripObservation, ...]:
    """Convert an IVS export frame into validated trip observations.

    Row identifiers are generated from the positional row number of the export.
    They label a record for auditing; they are never used to match a record
    against a reference table.
    """
    dataset = source_dataset.strip()
    if not dataset:
        raise ValidationError("source_dataset must not be empty")
    require_columns(frame, set(IVS_COLUMNS), "IVS trips")
    if frame.empty:
        raise ValidationError("IVS trips table contains no data rows")
    trips: list[TripObservation] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        loading_code = _code_text(row, "v28_beladingscode")
        trips.append(
            TripObservation(
                source_row_id=optional_text_value(row, "source_row_id", f"{dataset}:{position}"),
                origin_locode=text_value(row, "unlo_herkomst"),
                destination_locode=text_value(row, "unlo_bestemming"),
                vessel_type_id=integer_value(row, "v15_1_scheepstype_rws"),
                capacity_t=number_value(row, "v18_laadvermogen"),
                cargo_t=_cargo_tonnes(row, loading_code),
                containers_teu=optional_number_value(row, "v30_4_containers_teu_s"),
                cargo_code=_code_text(row, "nstr_nw"),
                source_dataset=dataset,
                loading_code=loading_code,
                observed_on=optional_text_value(row, "v05_06_begindt_evenement_iso"),
            )
        )
    return tuple(trips)


def load_ivs_trips_csv(
    path: str | Path, *, source_dataset: str | None = None
) -> tuple[TripObservation, ...]:
    """Load an IVS export from CSV, defaulting the dataset label to the file stem."""
    source = Path(path)
    table = read_csv_table(source)
    return ivs_trips_from_frame(table, source_dataset=source_dataset or source.stem)


def load_route_profiles_csv(path: str | Path) -> tuple[RouteProfile, ...]:
    """Load the origin-destination route profile that supplies distance and class."""
    table = read_csv_table(path)
    require_columns(table, ROUTE_PROFILE_COLUMNS, "route profiles")
    return tuple(
        RouteProfile(
            origin_locode=text_value(row, "origin_locode"),
            destination_locode=text_value(row, "destination_locode"),
            distance_km=number_value(row, "distance_km"),
            waterway_cemt_class=text_value(row, "waterway_cemt_class"),
            realised_speed_km_per_h=number_value(row, "realised_speed_km_per_h"),
            source=text_value(row, "source"),
        )
        for _, row in table.iterrows()
    )


def load_vessel_type_map_csv(path: str | Path) -> dict[int, str]:
    """Load the numeric vessel-type to RWS-class mapping, rejecting conflicts."""
    table = read_csv_table(path)
    require_columns(table, VESSEL_TYPE_COLUMNS, "vessel-type mapping")
    mapping: dict[int, str] = {}
    for _, row in table.iterrows():
        vessel_type_id = integer_value(row, "ship_type_id")
        rws_class = text_value(row, "rws_class").strip()
        if not rws_class:
            raise ValidationError(f"Empty rws_class for ship_type_id={vessel_type_id}")
        if vessel_type_id in mapping and mapping[vessel_type_id] != rws_class:
            raise ValidationError(
                f"Conflicting vessel classes for ship_type_id={vessel_type_id}: "
                f"{mapping[vessel_type_id]!r} and {rws_class!r}"
            )
        mapping[vessel_type_id] = rws_class
    if not mapping:
        raise ValidationError("Vessel-type mapping contains no usable rows")
    return mapping


def load_appearance_map_csv(path: str | Path) -> dict[str, AppearanceRule]:
    """Load an NSTR chapter to appearance mapping with an explicit source column."""
    table = read_csv_table(path)
    require_columns(table, APPEARANCE_MAP_COLUMNS, "appearance mapping")
    mapping: dict[str, AppearanceRule] = {}
    for _, row in table.iterrows():
        chapter = text_value(row, "nstr_chapter").strip()
        if len(chapter) != 1 or not chapter.isdigit():
            raise ValidationError(f"nstr_chapter must be a single digit; received {chapter!r}")
        rule = AppearanceRule(
            appearance=text_value(row, "appearance"), source=text_value(row, "source")
        )
        if chapter in mapping and mapping[chapter] != rule:
            raise ValidationError(
                f"Conflicting appearance rule for nstr_chapter {chapter!r}: "
                f"{mapping[chapter]!r} and {rule!r}"
            )
        mapping[chapter] = rule
    if not mapping:
        raise ValidationError("Appearance mapping contains no usable rows")
    return mapping


def arc_requests_to_frame(requests: Iterable[ArcCostRequest]) -> pd.DataFrame:
    """Build the runner arc-request table, keeping provenance columns at the end."""
    records = [_arc_request_to_record(request) for request in requests]
    if not records:
        raise ValidationError("No arc-cost requests to write")
    frame = pd.DataFrame.from_records(records)
    provenance = [column for column in frame.columns if column not in ARC_REQUEST_COLUMNS]
    return frame[[*ARC_REQUEST_COLUMNS, *provenance]]


def trip_rejections_to_frame(rejections: Iterable[TripRejection]) -> pd.DataFrame:
    """Build a flat rejection table with reason codes and detail messages."""
    records = [
        {
            "source_row_id": rejection.source_row_id,
            "reason": rejection.reason.value,
            "detail": rejection.detail,
        }
        for rejection in rejections
    ]
    return pd.DataFrame.from_records(
        records, columns=["source_row_id", "reason", "detail"] if not records else None
    )


def _arc_request_to_record(request: ArcCostRequest) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in fields(request):
        value = getattr(request, field.name)
        record[field.name] = "; ".join(value) if field.name == "notes" else value
    return record


def _cargo_tonnes(row: pd.Series, loading_code: str) -> float | None:
    """Convert the transported weight to tonnes, or return None when unknown."""
    value = row["v38_vervoerd_gewicht"]
    if pd.isna(value):
        return 0.0 if loading_code == IVS_EMPTY_LOADING_CODE else None
    return number_value(row, "v38_vervoerd_gewicht") * CARGO_WEIGHT_KG_TO_T


def _code_text(row: pd.Series, column: str) -> str:
    """Return a coded value as text, formatting whole numbers without a decimal part."""
    if column not in row.index:
        return ""
    value = row[column]
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    return str(value).strip()
