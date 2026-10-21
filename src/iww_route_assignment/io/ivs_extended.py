"""Extended IVS row conversion. Observed journey columns are deliberately unused."""

from collections.abc import Mapping

import pandas as pd

from ..domain.models import AppearanceRule, TripObservation
from ..exceptions import ValidationError
from ._frames import integer_value, number_value, optional_text_value, text_value

EXTENDED_COLUMNS = {
    "Jaar",
    "Pseudo_ReisID",
    "SK_CODE",
    "v28_Beladingscode",
    "Segment",
    "Lading",
    "Aantal_Schepen",
    "Laadvermogen",
    "UNLO_Herkomst",
    "UNLO_Bestemming",
}
DEFAULT_SEGMENT_MAP: Mapping[str, AppearanceRule] = {
    "Tankvaart": AppearanceRule("natte_bulk", "mapping:ivs-segment-v1"),
    "Droge bulkvaart": AppearanceRule("droge_bulk", "mapping:ivs-segment-v1"),
    "Containervaart": AppearanceRule("container", "mapping:ivs-segment-v1"),
}


def extended_identifier(row: pd.Series, dataset: str) -> str:
    """Namespace the original identifier by dataset and year; never coerce it to a number."""
    identifier = text_value(row, "Pseudo_ReisID").strip()
    if not identifier:
        raise ValidationError("Pseudo_ReisID must not be empty")
    return f"{dataset}:{integer_value(row, 'Jaar')}:{identifier}"


def extended_trip(
    row: pd.Series,
    dataset: str,
    identifier: str,
    unit: str,
    mapping: Mapping[str, AppearanceRule],
) -> TripObservation:
    """Convert one row with explicitly declared Lading units and sourced segment mapping."""
    if integer_value(row, "Aantal_Schepen") != 1:
        raise ValidationError("Aantal_Schepen must be 1; other count semantics are unconfirmed")
    code = str(integer_value(row, "v28_Beladingscode"))
    if pd.isna(row["Lading"]):
        cargo = 0.0 if code == "1" else None
    else:
        cargo = number_value(row, "Lading") * (0.001 if unit == "kg" else 1.0)
    if code == "1" and cargo not in {None, 0.0}:
        raise ValidationError("Empty loading code contradicts positive Lading")
    segment = optional_text_value(row, "Segment").strip()
    rule = mapping.get(segment.casefold())
    if segment and rule is None:
        raise ValidationError(f"Unmapped Segment {segment!r}; supply a sourced segment mapping")
    dates = []
    for column, date_format in (
        ("Begindtijd_evenement", "%d%b%Y:%H:%M:%S"),
        ("Begintijd_evenement", None),
    ):
        text = optional_text_value(row, column).strip()
        if text:
            try:
                dates.append(pd.to_datetime(text, format=date_format))
            except (ValueError, TypeError) as error:
                raise ValidationError(f"Invalid {column}: {text!r}") from error
    if len(dates) == 2 and dates[0] != dates[1]:
        raise ValidationError("The two event-time columns disagree")
    vessel = text_value(row, "SK_CODE").strip()
    if not vessel:
        raise ValidationError("SK_CODE must not be empty")
    return TripObservation(
        identifier,
        text_value(row, "UNLO_Herkomst"),
        text_value(row, "UNLO_Bestemming"),
        None,
        number_value(row, "Laadvermogen"),
        cargo,
        None,
        "",
        dataset,
        loading_code=code,
        observed_on=dates[0].isoformat() if dates else "",
        vessel_rws_class=vessel,
        appearance=rule.appearance if rule else "",
        appearance_source=rule.source if rule else "",
        cargo_unit_source=f"assumption:declared-Lading-unit={unit};capacity=t",
        notes=("Aantal_Schepen=1;not-a-cost-multiplier", f"Segment={segment}"),
    )
