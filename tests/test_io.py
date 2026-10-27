import csv
from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import (
    ArcTravelInput,
    LoadingCategory,
    ValidationError,
    calculate_arc_cost,
)
from iww_route_assignment.io import (
    arc_cost_to_record,
    load_cost_rates_csv,
    load_fuel_observations_csv,
    load_fuel_provider,
    load_vessel_references_csv,
    select_cost_rates,
    summarise_fuel_resolutions,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_validated_loaders_and_selection() -> None:
    fuel = load_fuel_observations_csv(FIXTURES / "fuel_observations.csv")
    vessels = load_vessel_references_csv(FIXTURES / "vessel_references.csv")
    rates = load_cost_rates_csv(FIXTURES / "cost_rates.csv")
    assert len(fuel) == 4
    assert len(vessels) == 4
    assert select_cost_rates(rates, "dry BULK", "M1").maintenance_eur_per_km == 3.0


def test_load_fuel_provider_convenience() -> None:
    provider = load_fuel_provider(
        FIXTURES / "fuel_observations.csv", FIXTURES / "vessel_references.csv"
    )
    result = provider.resolve("M1", "I", loading_category=LoadingCategory.LIGHT)
    assert result.fuel_rate_l_per_h == 40.0


def test_generated_index_csv_is_not_silently_treated_as_normalised(
    tmp_path: Path,
) -> None:
    path = tmp_path / "generated-index.csv"
    path.write_text(
        ",source_row_id,vessel_rws_class\n0,row-1,M1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="generated index columns"):
        load_fuel_observations_csv(path)


def test_missing_csv_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        load_cost_rates_csv(tmp_path / "missing.csv")


def test_missing_required_columns_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.csv"
    path.write_text("source_row_id\nrow-1\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="missing required columns"):
        load_fuel_observations_csv(path)


def test_duplicate_cost_keys_are_rejected(tmp_path: Path) -> None:
    table = pd.read_csv(FIXTURES / "cost_rates.csv")
    table = pd.concat((table, table.iloc[[0]]), ignore_index=True)
    path = tmp_path / "duplicate.csv"
    table.to_csv(path, index=False)
    with pytest.raises(ValidationError, match="Duplicate cost-rate key"):
        load_cost_rates_csv(path)


def test_missing_numeric_value_is_rejected(tmp_path: Path) -> None:
    table = pd.read_csv(FIXTURES / "cost_rates.csv")
    table.loc[0, "labor_h"] = None
    path = tmp_path / "missing-value.csv"
    table.to_csv(path, index=False)
    with pytest.raises(ValidationError, match="missing value"):
        load_cost_rates_csv(path)


def test_flat_result_and_fuel_summary(cost_rates, scenario, fuel_provider) -> None:
    observed = fuel_provider.resolve("M1", "I", loading_category=LoadingCategory.LIGHT)
    proxied = fuel_provider.resolve("M3", "Va", loading_category=LoadingCategory.LIGHT)
    result = calculate_arc_cost(ArcTravelInput(10.0, 10.0), cost_rates, scenario, observed)
    record = arc_cost_to_record("arc-1", result)
    summary = summarise_fuel_resolutions((observed, proxied))

    assert record["arc_id"] == "arc-1"
    assert record["fuel_method"] == "observed"
    assert record["fuel_source_row_ids"] == '["general:M1:I"]'
    assert summary["total"] == 2
    assert summary["low_or_fallback_count"] == 1
    assert summary["low_or_fallback_share"] == 0.5


def test_integration_output_has_stable_columns(
    tmp_path: Path, cost_rates, scenario, fuel_provider
) -> None:
    rows = []
    for index, ratio in enumerate((0.0, 0.5, 1.0), start=1):
        fuel = fuel_provider.resolve("M1", "II", loading_ratio=ratio)
        result = calculate_arc_cost(
            ArcTravelInput(float(index * 10), 10.0), cost_rates, scenario, fuel
        )
        rows.append(arc_cost_to_record(f"arc-{index}", result))
    path = tmp_path / "costs.csv"
    pd.DataFrame(rows).to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
    loaded = pd.read_csv(path)
    assert loaded["arc_id"].tolist() == ["arc-1", "arc-2", "arc-3"]
    assert loaded["fuel_method"].tolist() == ["waterway_interpolated"] * 3
    assert loaded["reported_variable_cost_eur"].is_monotonic_increasing
