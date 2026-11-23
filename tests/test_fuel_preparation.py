from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import DuplicateFuelObservationError, ValidationError
from iww_route_assignment.io import (
    load_fuel_observations_csv,
    prepare_notebook_fuel_observations,
    write_notebook_fuel_observations_csv,
)


def test_preparation_explicitly_selects_one_source_without_merging() -> None:
    general = _raw_row("M6", "IV", 161.0, 210.0, 222.0)
    container = _raw_row("M6", "IV", 140.0, 174.0, 203.0)
    result = prepare_notebook_fuel_observations(
        general, container, selected_source="general", value_unit="l_per_km"
    )

    assert "vessel_profile" not in result
    assert result["source_row_id"].is_unique
    assert set(result["diesel_light_l_per_km"]) == {161.0}
    alternate = prepare_notebook_fuel_observations(
        general, container, selected_source="container", value_unit="l_per_km"
    )
    assert alternate["diesel_light_l_per_km"].tolist() == [140.0]
    with pytest.raises(ValidationError, match="selected_source"):
        prepare_notebook_fuel_observations(
            general, container, selected_source="both", value_unit="l_per_km"
        )


def test_preparation_does_not_generate_interpolated_rows() -> None:
    general = pd.concat(
        (
            _raw_row("M1", "I", 37.0, 50.0, 58.0),
            _raw_row("M1", "Va", 49.0, 61.0, 64.0),
        ),
        ignore_index=True,
    )
    container = _raw_row("M3", "III", 52.0, 66.0, 79.0)
    result = prepare_notebook_fuel_observations(
        general, container, selected_source="general", value_unit="l_per_km"
    )
    assert len(result) == 2
    assert "II" not in result["waterway_cemt_class"].tolist()


def test_preparation_rejects_duplicate_within_profile() -> None:
    row = _raw_row("M1", "I", 37.0, 50.0, 58.0)
    duplicated = pd.concat((row, row), ignore_index=True)
    with pytest.raises(DuplicateFuelObservationError, match="within the selected source"):
        prepare_notebook_fuel_observations(
            duplicated,
            _raw_row("M3", "III", 1, 2, 3),
            selected_source="general",
            value_unit="l_per_km",
        )


def test_write_is_index_free_and_loadable(tmp_path: Path) -> None:
    output = write_notebook_fuel_observations_csv(
        _raw_row("BII-6B", "VIC", 1, 2, 3),
        _raw_row("M3", "III", 4, 5, 6),
        tmp_path / "fuel.csv",
        selected_source="general",
        value_unit="l_per_km",
    )
    assert not output.read_text(encoding="utf-8").splitlines()[0].startswith(",")
    observations = load_fuel_observations_csv(output)
    assert observations[0].vessel_rws_class == "BII-6b"
    assert observations[0].waterway_cemt_class == "VIc"


def test_preparation_rejects_missing_columns() -> None:
    with pytest.raises(ValidationError, match="missing columns"):
        prepare_notebook_fuel_observations(
            pd.DataFrame({"vessel_rws_class": ["M1"]}),
            _raw_row("M3", "III", 1, 2, 3),
            selected_source="general",
            value_unit="l_per_km",
        )


def test_megajoules_per_km_are_divided_by_the_energy_of_diesel() -> None:
    # 36, 54 and 72 MJ/km at 36 MJ per litre are 1.0, 1.5 and 2.0 litres per km.
    general = _raw_row("M1", "I", 36.0, 54.0, 72.0)
    container = _raw_row("M3", "III", 1, 2, 3)
    result = prepare_notebook_fuel_observations(
        general, container, selected_source="general", value_unit="MJ_per_km"
    )
    row = result.iloc[0]
    assert (row["diesel_light_l_per_km"], row["diesel_medium_l_per_km"]) == (1.0, 1.5)
    assert row["diesel_heavy_l_per_km"] == 2.0
    assert row["source_dataset"] == "notebook:general; MJ per km divided by 36 MJ per litre"
    assert row["source_row_id"] == "notebook:general:M1:I"
    other = prepare_notebook_fuel_observations(
        general,
        container,
        selected_source="general",
        value_unit="MJ_per_km",
        diesel_energy_mj_per_l=40.0,
    )
    assert other["diesel_light_l_per_km"].iloc[0] == pytest.approx(0.9)
    assert "divided by 40 MJ per litre" in other["source_dataset"].iloc[0]


def test_litres_per_km_pass_through_unchanged() -> None:
    general = _raw_row("M1", "I", 36.0, 54.0, 72.0)
    result = prepare_notebook_fuel_observations(
        general, _raw_row("M3", "III", 1, 2, 3), selected_source="general", value_unit="l_per_km"
    )
    assert result["diesel_light_l_per_km"].tolist() == [36.0]
    assert result["source_dataset"].tolist() == ["notebook:general"]  # no conversion recorded


def test_value_unit_must_be_stated_and_known() -> None:
    general = _raw_row("M1", "I", 36.0, 54.0, 72.0)
    container = _raw_row("M3", "III", 1, 2, 3)
    with pytest.raises(TypeError, match="value_unit"):
        prepare_notebook_fuel_observations(  # type: ignore[call-arg]
            general, container, selected_source="general"
        )
    for unit in ("l_per_h", "MJ", ""):
        with pytest.raises(ValidationError, match="value_unit must be one of"):
            prepare_notebook_fuel_observations(
                general, container, selected_source="general", value_unit=unit
            )
    for energy in (0.0, -36.0, float("nan"), float("inf")):
        with pytest.raises(ValidationError, match="diesel_energy_mj_per_l must be positive"):
            prepare_notebook_fuel_observations(
                general,
                container,
                selected_source="general",
                value_unit="MJ_per_km",
                diesel_energy_mj_per_l=energy,
            )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("vessel_rws_class", " ", "contains an empty value"),
        ("diesel_light", None, "contains a missing value"),
        ("diesel_medium", "much", "non-numeric value"),
        ("diesel_heavy", -1.0, "finite and non-negative"),
        ("diesel_heavy", float("inf"), "finite and non-negative"),
    ],
)
def test_preparation_rejects_bad_values(column: str, value: object, message: str) -> None:
    general = _raw_row("M1", "I", 36.0, 54.0, 72.0).astype(object)
    general.loc[0, column] = value
    with pytest.raises(ValidationError, match=message):
        prepare_notebook_fuel_observations(
            general,
            _raw_row("M3", "III", 1, 2, 3),
            selected_source="general",
            value_unit="MJ_per_km",
        )


def test_preparation_rejects_empty_table_and_empty_source_name() -> None:
    general = _raw_row("M1", "I", 36.0, 54.0, 72.0)
    container = _raw_row("M3", "III", 1, 2, 3)
    with pytest.raises(ValidationError, match="contains no rows"):
        prepare_notebook_fuel_observations(
            general.iloc[0:0], container, selected_source="general", value_unit="l_per_km"
        )
    with pytest.raises(ValidationError, match="source dataset must not be empty"):
        prepare_notebook_fuel_observations(
            general,
            container,
            selected_source="general",
            value_unit="l_per_km",
            general_source=" ",
        )


def _raw_row(vessel: str, waterway: str, light: float, medium: float, heavy: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "vessel_rws_class": [vessel],
            "waterway_cemt_class": [waterway],
            "diesel_light": [light],
            "diesel_medium": [medium],
            "diesel_heavy": [heavy],
        }
    )
