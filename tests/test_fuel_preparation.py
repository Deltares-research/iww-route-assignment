from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import DuplicateFuelObservationError, ValidationError
from iww_route_assignment.io import (
    load_fuel_observations_csv,
    prepare_notebook_fuel_observations,
    write_notebook_fuel_observations_csv,
)


def test_preparation_preserves_general_and_container_profiles() -> None:
    general = _raw_row("M6", "IV", 161.0, 210.0, 222.0)
    container = _raw_row("M6", "IV", 140.0, 174.0, 203.0)
    result = prepare_notebook_fuel_observations(general, container)

    assert result["vessel_profile"].tolist() == ["container", "general"]
    assert result["source_row_id"].is_unique
    assert set(result["diesel_light_l_per_h"]) == {140.0, 161.0}


def test_preparation_does_not_generate_interpolated_rows() -> None:
    general = pd.concat(
        (
            _raw_row("M1", "I", 37.0, 50.0, 58.0),
            _raw_row("M1", "Va", 49.0, 61.0, 64.0),
        ),
        ignore_index=True,
    )
    container = _raw_row("M3", "III", 52.0, 66.0, 79.0)
    result = prepare_notebook_fuel_observations(general, container)
    assert len(result) == 3
    assert "II" not in result["waterway_cemt_class"].tolist()


def test_preparation_rejects_duplicate_within_profile() -> None:
    row = _raw_row("M1", "I", 37.0, 50.0, 58.0)
    duplicated = pd.concat((row, row), ignore_index=True)
    with pytest.raises(DuplicateFuelObservationError, match="within a profile"):
        prepare_notebook_fuel_observations(duplicated, _raw_row("M3", "III", 1, 2, 3))


def test_write_is_index_free_and_loadable(tmp_path: Path) -> None:
    output = write_notebook_fuel_observations_csv(
        _raw_row("BII-6B", "VIC", 1, 2, 3),
        _raw_row("M3", "III", 4, 5, 6),
        tmp_path / "fuel.csv",
    )
    assert not output.read_text(encoding="utf-8").splitlines()[0].startswith(",")
    observations = load_fuel_observations_csv(output)
    assert observations[1].vessel_rws_class == "BII-6b"
    assert observations[1].waterway_cemt_class == "VIc"


def test_preparation_rejects_missing_columns() -> None:
    with pytest.raises(ValidationError, match="missing columns"):
        prepare_notebook_fuel_observations(
            pd.DataFrame({"vessel_rws_class": ["M1"]}),
            _raw_row("M3", "III", 1, 2, 3),
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
