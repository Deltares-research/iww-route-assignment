"""Two explicit IVS layouts, one audited observation contract."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from ..domain import AppearanceRule, IvsFormat, TripObservation, TripRejection, TripRejectionReason
from ..exceptions import ValidationError
from ._frames import integer_value, number_value, optional_text_value, require_columns, text_value
from .ivs_extended import DEFAULT_SEGMENT_MAP, EXTENDED_COLUMNS, extended_identifier, extended_trip

IVS_COLUMNS = {
    "unlo_herkomst": "origin_locode",
    "unlo_bestemming": "destination_locode",
    "v15_1_scheepstype_rws": "vessel_type_id",
    "v18_laadvermogen": "capacity_t",
    "v38_vervoerd_gewicht": "cargo_t",
    "v30_4_containers_teu_s": "containers_teu",
    "nstr_nw": "cargo_code",
}


@dataclass(frozen=True, slots=True)
class IvsReadResult:
    """Valid observations and rejected rows; schema/identity errors fail the read."""

    trips: tuple[TripObservation, ...]
    rejections: tuple[TripRejection, ...]


def read_ivs_frame(
    frame: pd.DataFrame,
    *,
    source_dataset: str,
    format: IvsFormat | str = IvsFormat.WEEKMONITOR,
    cargo_weight_unit: str | None = None,
    segment_map: Mapping[str, AppearanceRule] | None = None,
    drop_export_index: bool = False,
    row_offset: int = 0,
) -> IvsReadResult:
    """Audit malformed rows without treating unknown data as zero or repairing IDs."""
    try:
        layout = IvsFormat(format)
    except ValueError as error:
        raise ValidationError(f"Unsupported IVS format {format!r}") from error
    dataset = source_dataset.strip()
    if not dataset:
        raise ValidationError("source_dataset must not be empty")
    if layout is IvsFormat.EXTENDED_2026 and cargo_weight_unit not in {"t", "kg"}:
        raise ValidationError("extended_2026 requires cargo_weight_unit=t or kg")
    if layout is IvsFormat.WEEKMONITOR and cargo_weight_unit not in {None, "kg"}:
        raise ValidationError("weekmonitor cargo_weight_unit is kg")
    indices = [str(c) for c in frame.columns if not str(c) or str(c).startswith("Unnamed:")]
    if indices and (not drop_export_index or indices not in [["Unnamed: 0"], [""]]):
        raise ValidationError(
            "Generated index columns require explicit drop_export_index=True for one export index"
        )
    require_columns(
        frame,
        EXTENDED_COLUMNS if layout is IvsFormat.EXTENDED_2026 else set(IVS_COLUMNS),
        "IVS trips",
    )
    if frame.empty:
        raise ValidationError("IVS trips table contains no data rows")
    source_mapping = DEFAULT_SEGMENT_MAP if segment_map is None else segment_map
    mapping: dict[str, AppearanceRule] = {}
    for key, rule in source_mapping.items():
        key = key.strip().casefold()
        if key in mapping and mapping[key] != rule:
            raise ValidationError(f"Conflicting segment mapping {key!r}")
        mapping[key] = rule
    trips: list[TripObservation] = []
    rejected: list[TripRejection] = []
    seen: set[str] = set()
    for position, (_, row) in enumerate(frame.iterrows(), start=row_offset):
        identifier = f"{dataset}:{position}"
        try:
            identifier = (
                extended_identifier(row, dataset)
                if layout is IvsFormat.EXTENDED_2026
                else optional_text_value(row, "source_row_id", identifier).strip()
            )
        except ValidationError as error:
            rejected.append(
                TripRejection(identifier, TripRejectionReason.INVALID_REQUEST, str(error))
            )
            continue
        if identifier in seen:
            raise ValidationError(
                f"Duplicate source_row_id {identifier!r}; resolve source identity explicitly"
            )
        seen.add(identifier)
        try:
            trip = (
                extended_trip(row, dataset, identifier, str(cargo_weight_unit), mapping)
                if layout is IvsFormat.EXTENDED_2026
                else _weekly_trip(row, dataset, identifier)
            )
            if indices:
                trip = replace(trip, notes=(*trip.notes, "dropped_export_index:" + indices[0]))
            trips.append(trip)
        except ValidationError as error:
            rejected.append(
                TripRejection(identifier, TripRejectionReason.INVALID_REQUEST, str(error))
            )
    return IvsReadResult(tuple(trips), tuple(rejected))


def ivs_trips_from_frame(
    frame: pd.DataFrame,
    *,
    source_dataset: str,
    format: IvsFormat | str = IvsFormat.WEEKMONITOR,
    cargo_weight_unit: str | None = None,
    drop_export_index: bool = False,
) -> tuple[TripObservation, ...]:
    """Strict adapter; read_ivs_frame provides row-level rejection reports instead."""
    result = read_ivs_frame(
        frame,
        source_dataset=source_dataset,
        format=format,
        cargo_weight_unit=cargo_weight_unit,
        drop_export_index=drop_export_index,
    )
    if result.rejections:
        raise ValidationError(result.rejections[0].detail)
    return result.trips


def load_ivs_trips_csv(
    path: str | Path,
    *,
    source_dataset: str | None = None,
    format: IvsFormat | str = IvsFormat.WEEKMONITOR,
    cargo_weight_unit: str | None = None,
    drop_export_index: bool = False,
) -> tuple[TripObservation, ...]:
    """Read raw exports as text, preserving identifiers and recording unit conversion."""
    source = Path(path)
    return ivs_trips_from_frame(
        pd.read_csv(source, dtype="string"),
        source_dataset=source_dataset or source.stem,
        format=format,
        cargo_weight_unit=cargo_weight_unit,
        drop_export_index=drop_export_index,
    )


def _weekly_trip(row: pd.Series, dataset: str, identifier: str) -> TripObservation:
    loading = (
        ""
        if pd.isna(row.get("v28_beladingscode"))
        else str(integer_value(row, "v28_beladingscode"))
    )
    vessel = optional_text_value(row, "sk_code").strip()
    cargo = (
        (0.0 if loading == "1" else None)
        if pd.isna(row["v38_vervoerd_gewicht"])
        else number_value(row, "v38_vervoerd_gewicht") * 0.001
    )
    if loading == "1" and cargo not in {None, 0.0}:
        raise ValidationError("Empty loading code contradicts positive cargo weight")
    return TripObservation(
        identifier,
        text_value(row, "unlo_herkomst"),
        text_value(row, "unlo_bestemming"),
        integer_value(row, "v15_1_scheepstype_rws"),
        number_value(row, "v18_laadvermogen"),
        cargo,
        None
        if pd.isna(row["v30_4_containers_teu_s"])
        else number_value(row, "v30_4_containers_teu_s"),
        "" if pd.isna(row["nstr_nw"]) else str(integer_value(row, "nstr_nw")),
        dataset,
        loading_code=loading,
        observed_on=optional_text_value(row, "v05_06_begindt_evenement_iso"),
        vessel_rws_class=vessel,
        cargo_unit_source="preset:weekmonitor:kg-to-t;capacity=t",
    )
