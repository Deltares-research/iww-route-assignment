"""Parameter extraction and the shared inputs, on tiny synthetic sources.

Energy rows are megajoules per km. Diesel holds 36 MJ per litre, so 36, 54 and 72 MJ/km are
1.0, 1.5 and 2.0 litres per km.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from test_bivas import make_bivas_db

from iww_route_assignment import ScenarioCostParameters, ValidationError
from iww_route_assignment.io import (
    ParameterSources,
    build_shared_inputs,
    compare_ship_type_sources,
    extract_parameter_tables,
    fuel_observations_from_energy,
    load_cost_inputs,
    load_vessel_catalogue,
    read_bivas_ship_type_table,
    read_dtv_vessel_table,
    read_energy_use_from_notebook,
    sha256_file,
)
from iww_route_assignment.io.reference_build import (
    AIR_DRAUGHT_RULE,
    ENERGY_UNIT_RULE,
    FUEL_SOURCE_RULE,
    vessel_tables,
)

DTV_RECORDS = [
    {
        # The percentiles and the empty draught equal the BIVAS ship_types row of M1.
        "RWS-class": "M1",
        "CEMT-class": "I",
        "Load weight maximum [ton]": 350.0,
        "Beam [m]": 5.05,
        "Beam 10% percentile [m]": 5.04,
        "Beam 50% percentile [m]": 5.06,
        "Beam 90% percentile [m]": 5.09,
        "Length [m]": 38.5,
        "Length 10% percentile [m]": 38.62,
        "Length 50% percentile [m]": 38.89,
        "Length 90% percentile [m]": 39.53,
        "Draught loaded [m]": 2.2,
        "Draught empty [m]": 1.0,
        "Height average [m]": 4.0,
    },
    {
        # DTV writes a lower-case b where BIVAS writes BII-2B. Capacity is unknown.
        # BIVAS gives class Va and a median length of 170 m. Width percentiles are absent.
        "RWS-class": "BII-2b",
        "CEMT-class": "IVa",
        "Load weight maximum [ton]": None,
        "Beam [m]": 11.4,
        "Length [m]": 170.0,
        "Length 50% percentile [m]": 168.0,
        "Draught loaded [m]": 3.0,
        "Draught empty [m]": 1.5,
        "Height average [m]": 6.0,
    },
    # In DTV only: BIVAS has no ship type, so no cost rates, for this class.
    {"RWS-class": "BIIa-1", "CEMT-class": "Va", "Beam [m]": 11.4, "Length [m]": 110.0},
    {"RWS-class": None, "CEMT-class": "I"},
]
GENERAL = [
    ("Motorvessel", "M1", "I", 36.0, 54.0, 72.0),
    ("Motorvessel", "M1", "Va", 45.0, 63.0, 81.0),
]
CONTAINER = [
    ("Motorvessel", "M1", "I", 360.0, 540.0, 720.0),  # never used: M1 is in the general table
    ("Motorvessel", "M3", "III", 72.0, 90.0, 108.0),
]


def _energy_rows(rows: list[tuple[str, str, str, float, float, float]]) -> list[dict[str, object]]:
    names = (
        "vessel_category",
        "vessel_rws_class",
        "waterway_cemt_class",
        "diesel_light",
        "diesel_medium",
        "diesel_heavy",
    )
    return [dict(zip(names, row, strict=True)) for row in rows]


def _write_notebook(
    path: Path,
    general: list[tuple[str, str, str, float, float, float]] | None = None,
    container: list[tuple[str, str, str, float, float, float]] | None = None,
    *,
    with_container: bool = True,
) -> Path:
    cells: list[dict[str, object]] = [
        {"cell_type": "markdown", "source": ["# Fuel table\n"]},
        {"cell_type": "code", "source": ["%matplotlib inline\n"]},  # not Python: skipped
        {"cell_type": "code", "source": ["import pandas as pd\n", "x, y = 1, 2\n"]},
        {
            "cell_type": "code",
            "source": [
                f"vessel_data = {_energy_rows(GENERAL if general is None else general)!r}\n"
            ],
        },
    ]
    if with_container:
        rows = _energy_rows(CONTAINER if container is None else container)
        cells.append({"cell_type": "code", "source": [f"container_vessel_data = {rows!r}\n"]})
    path.write_text(json.dumps({"cells": cells}), encoding="utf-8")
    return path


@pytest.fixture
def sources(tmp_path: Path) -> ParameterSources:
    dtv = tmp_path / "DTV_shiptypes_database.json"
    dtv.write_text(json.dumps(DTV_RECORDS), encoding="utf-8")
    return ParameterSources(
        bivas_db=make_bivas_db(tmp_path / "Bivas.db"),
        dtv_ship_types=dtv,
        energy_notebook=_write_notebook(tmp_path / "fuel.ipynb"),
    )


def _ship_types() -> pd.DataFrame:
    return pd.DataFrame({"ship_type_id": [2, 7], "rws_class": ["M1", "BII-2B"]})


def test_dtv_table_joins_without_regard_to_letter_case(sources: ParameterSources) -> None:
    table = read_dtv_vessel_table(sources.dtv_ship_types, _ship_types())
    assert table["rws_class"].tolist() == ["M1", "BII-2B"]  # the BIVAS spelling is kept
    assert table["ship_type_id"].tolist() == [2, 7]
    assert table["cemt_class_raw"].tolist() == ["I", "IVa"]
    assert table["cemt_class"].tolist() == ["I", "IV"]  # IVa is folded into IV
    first = table.iloc[0]
    assert (first["capacity_t"], first["beam_m"], first["length_m"]) == (350.0, 5.05, 38.5)
    assert (first["draught_loaded_m"], first["draught_empty_m"], first["height_m"]) == (
        2.2,
        1.0,
        4.0,
    )
    assert pd.isna(table.loc[1, "capacity_t"])
    assert table["source"].str.startswith("DTV shiptypes database").all()


def test_dtv_table_stops_on_missing_file_or_ship_type(
    tmp_path: Path, sources: ParameterSources
) -> None:
    with pytest.raises(ValidationError, match="DTV ship-type file not found"):
        read_dtv_vessel_table(tmp_path / "missing.json", _ship_types())
    unknown = pd.DataFrame({"ship_type_id": [9], "rws_class": ["M99"]})
    with pytest.raises(ValidationError, match="'M99' is not in the DTV file"):
        read_dtv_vessel_table(sources.dtv_ship_types, unknown)
    repeated = tmp_path / "repeated.json"
    twice = [DTV_RECORDS[0], DTV_RECORDS[0] | {"RWS-class": "m1"}]
    repeated.write_text(json.dumps(twice), encoding="utf-8")
    with pytest.raises(ValidationError, match="repeats an RWS-class"):
        read_dtv_vessel_table(repeated, _ship_types())


def test_ship_type_sources_are_compared_class_by_class(sources: ParameterSources) -> None:
    bivas = read_bivas_ship_type_table(sources.bivas_db)
    comparison = compare_ship_type_sources(bivas, sources.dtv_ship_types)
    assert comparison["rws_class"].tolist() == ["M1", "BII-2B", "BIIa-1"]
    assert comparison["ship_type_id"].iloc[:2].tolist() == [2, 7]
    assert pd.isna(comparison["ship_type_id"].iloc[2])
    assert comparison["in_bivas"].tolist() == [True, True, False]
    assert comparison["in_dtv"].tolist() == [True, True, True]
    assert comparison["bivas_cemt_class"].iloc[:2].tolist() == ["I", "Va"]
    assert comparison["dtv_cemt_class"].tolist() == ["I", "IVa", "Va"]
    # IVa folds into IV, which is not the BIVAS class Va.
    assert comparison["cemt_agrees"].iloc[:2].tolist() == [True, False]
    assert pd.isna(comparison["cemt_agrees"].iloc[2])
    # M1 agrees on all seven shared values. BII-2B differs on the median length, 168 against
    # 170 m. Its width percentiles are absent in DTV, so they are not compared.
    assert comparison["shared_values_agree"].iloc[:2].tolist() == [True, False]
    assert comparison["differing_values"].tolist() == ["", "length_p50_m", ""]
    assert comparison["dtv_length_m"].tolist() == [38.5, 170.0, 110.0]
    assert comparison["bivas_length_p50_m"].iloc[:2].tolist() == [38.89, 170.0]


def test_ship_type_comparison_does_not_depend_on_row_order(sources: ParameterSources) -> None:
    bivas = read_bivas_ship_type_table(sources.bivas_db)
    forward = compare_ship_type_sources(bivas, sources.dtv_ship_types)
    backward = compare_ship_type_sources(bivas.iloc[::-1], sources.dtv_ship_types)
    pd.testing.assert_frame_equal(forward, backward)


def test_energy_tables_are_read_from_the_notebook_lists(sources: ParameterSources) -> None:
    energy = read_energy_use_from_notebook(sources.energy_notebook)
    assert energy["cargo_profile"].tolist() == ["general", "general", "container", "container"]
    assert energy["vessel_rws_class"].tolist() == ["M1", "M1", "M1", "M3"]
    first = energy.iloc[0]
    assert first["energy_light_mj_per_km"] == 36.0  # diesel_light
    assert first["energy_medium_mj_per_km"] == 54.0  # diesel_medium
    assert first["energy_heavy_mj_per_km"] == 72.0  # diesel_heavy
    assert "megajoules per km" in first["source"]


def test_energy_notebook_problems_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Fuel notebook not found"):
        read_energy_use_from_notebook(tmp_path / "missing.ipynb")
    incomplete = _write_notebook(tmp_path / "incomplete.ipynb", with_container=False)
    with pytest.raises(ValidationError, match="no list named.*container_vessel_data"):
        read_energy_use_from_notebook(incomplete)
    repeated = _write_notebook(tmp_path / "repeated.ipynb", general=[GENERAL[0], GENERAL[0]])
    with pytest.raises(ValidationError, match="repeats a vessel and waterway"):
        read_energy_use_from_notebook(repeated)


def test_extract_writes_four_tables_and_a_report(tmp_path: Path, sources: ParameterSources) -> None:
    written = extract_parameter_tables(sources, tmp_path / "parameters")
    assert sorted(written) == [
        "bivas_cemt_classes.csv",
        "ce_delft_energy_use.csv",
        "cost_rates_2021.csv",
        "rws_to_cemt.csv",
        "ship_type_comparison.csv",
    ]
    rates = pd.read_csv(written["cost_rates_2021.csv"])
    assert rates["labor_h"].tolist() == [101.0, 201.0, 301.0]  # cost set 3 only
    assert pd.read_csv(written["rws_to_cemt.csv"])["rws_class"].tolist() == ["M1", "BII-2B"]
    report = json.loads((tmp_path / "parameters" / "build_report.json").read_text("utf-8"))
    assert report["bivas_cost_set"] == 3
    assert report["input_files"] == {
        "bivas_db": "Bivas.db",
        "dtv_ship_types": "DTV_shiptypes_database.json",
        "energy_notebook": "fuel.ipynb",
    }
    assert report["input_sha256"]["bivas_db"] == sha256_file(sources.bivas_db)
    assert report["rows"] == {
        "bivas_cemt_classes.csv": 2,
        "ce_delft_energy_use.csv": 4,
        "cost_rates_2021.csv": 3,
        "rws_to_cemt.csv": 2,
        "ship_type_comparison.csv": 3,
    }
    assert "cemt_class" in report["bivas_tables"]
    assert report["ship_types_only_in_dtv"] == ["BIIa-1"]  # not in rws_to_cemt.csv
    assert report["ship_types_only_in_bivas"] == []
    assert report["cemt_class_differences"] == {"BII-2B": {"bivas": "Va", "dtv": "IVa"}}
    assert report["ship_type_value_differences"] == {"BII-2B": "length_p50_m"}
    assert str(tmp_path) not in json.dumps(report)
    other = extract_parameter_tables(sources, tmp_path / "other", cost_set=1)
    assert pd.read_csv(other["cost_rates_2021.csv"])["labor_h"].tolist() == [901.0]


def test_fuel_prefers_general_and_takes_container_only_for_absent_classes(
    sources: ParameterSources,
) -> None:
    energy = read_energy_use_from_notebook(sources.energy_notebook)
    fuel, classes = fuel_observations_from_energy(energy)
    assert classes == {"general": ["M1"], "container": ["M3"]}
    assert fuel["source_row_id"].tolist() == [
        "ce_delft:general:M1:I",
        "ce_delft:general:M1:Va",
        "ce_delft:container:M3:III",
    ]
    # 36, 54, 72 MJ/km divided by 36 MJ/l. The container row for M1 (360 MJ/km) is not used.
    m1 = fuel.iloc[0]
    assert (m1["diesel_light_l_per_km"], m1["diesel_medium_l_per_km"]) == (1.0, 1.5)
    assert m1["diesel_heavy_l_per_km"] == 2.0
    # M3 from the container table: 72, 90, 108 MJ/km are 2.0, 2.5, 3.0 l/km.
    m3 = fuel.iloc[2]
    assert (m3["diesel_light_l_per_km"], m3["diesel_medium_l_per_km"]) == (2.0, 2.5)
    assert m3["diesel_heavy_l_per_km"] == 3.0
    assert m1["source_dataset"].endswith(f"general table; {ENERGY_UNIT_RULE}")
    assert "container table" in m3["source_dataset"]
    assert ENERGY_UNIT_RULE == "conversion:MJ-per-km-divided-by-36-MJ-per-litre"
    half, _ = fuel_observations_from_energy(energy, diesel_energy_mj_per_l=72.0)
    assert half["diesel_light_l_per_km"].iloc[0] == 0.5


def test_fuel_does_not_depend_on_input_row_order(sources: ParameterSources) -> None:
    energy = read_energy_use_from_notebook(sources.energy_notebook)
    forward, forward_classes = fuel_observations_from_energy(energy)
    shuffled = energy.iloc[[3, 1, 2, 0]].reset_index(drop=True)
    backward, backward_classes = fuel_observations_from_energy(shuffled)
    pd.testing.assert_frame_equal(forward, backward)
    assert forward_classes == backward_classes


def test_fuel_rejects_bad_energy_tables(sources: ParameterSources) -> None:
    energy = read_energy_use_from_notebook(sources.energy_notebook)
    with pytest.raises(ValidationError, match="must be positive"):
        fuel_observations_from_energy(energy, diesel_energy_mj_per_l=0.0)
    with pytest.raises(ValidationError, match="no general-cargo rows"):
        fuel_observations_from_energy(energy[energy["cargo_profile"] == "container"])
    odd = energy.assign(waterway_cemt_class=energy["waterway_cemt_class"].replace("Va", "X"))
    with pytest.raises(ValidationError, match="Unsupported waterway classes.*X"):
        fuel_observations_from_energy(odd)
    repeated = pd.concat([energy, energy[energy["cargo_profile"] == "general"]])
    with pytest.raises(ValidationError, match="Duplicate fuel keys"):
        fuel_observations_from_energy(repeated)


def test_vessel_tables_drop_incomplete_classes_without_filling(
    sources: ParameterSources,
) -> None:
    vessels = read_dtv_vessel_table(sources.dtv_ship_types, _ship_types())
    references, states, dropped = vessel_tables(vessels)
    # BII-2B has no capacity: no reference, but its dimensions still give two states.
    assert references["rws_class"].tolist() == ["M1"]
    assert dropped == ["BII-2B"]
    assert states["vessel_id"].tolist() == [
        "M1-loaded",
        "M1-empty",
        "BII-2B-loaded",
        "BII-2B-empty",
    ]
    assert states["draught_m"].tolist() == [2.2, 1.0, 3.0, 1.5]
    assert states["air_draught_m"].tolist() == [4.0, 4.0, 6.0, 6.0]  # average height, both states
    assert states["source"].str.endswith(AIR_DRAUGHT_RULE).all()
    no_draught = vessels.assign(draught_empty_m=[0.0, 1.5])
    references, states, dropped = vessel_tables(no_draught)
    assert dropped == ["BII-2B", "M1"]
    assert "M1-empty" not in states["vessel_id"].tolist()
    unsupported = vessels.assign(cemt_class=["XX", "IV"])
    assert vessel_tables(unsupported)[2] == ["BII-2B", "M1"]


def test_build_shared_inputs_writes_files_the_model_loads(
    tmp_path: Path, sources: ParameterSources
) -> None:
    parameters = tmp_path / "parameters"
    extract_parameter_tables(sources, parameters)
    written = build_shared_inputs(parameters, tmp_path / "reference")
    assert sorted(written) == [
        "build_report",
        "cost_rates",
        "fuel_observations",
        "navigability_vessels",
        "vessel_references",
    ]
    assert all(path.is_file() for path in written.values())
    report = json.loads(written["build_report"].read_text("utf-8"))
    assert report["rules"] == [
        ENERGY_UNIT_RULE,
        FUEL_SOURCE_RULE,
        AIR_DRAUGHT_RULE,
        "fuel key is vessel and waterway only; no goods profile",
    ]
    assert report["fuel_rows"] == 3
    assert report["fuel_classes_from_general_table"] == ["M1"]
    assert report["fuel_classes_from_container_table"] == ["M3"]
    assert report["vessel_reference_rows"] == 1
    assert report["vessel_classes_dropped_for_missing_values"] == ["BII-2B"]
    assert report["navigability_vessel_states"] == 4
    assert report["input_files"]["cost_rates_2021.csv"] == "cost_rates_2021.csv"
    assert report["input_sha256"]["rws_to_cemt.csv"] == sha256_file(parameters / "rws_to_cemt.csv")

    # The written folder is a valid reference folder for a run.
    scenario = ScenarioCostParameters(1.0, 3.0, 1.0)
    costs = load_cost_inputs(tmp_path / "reference", scenario)
    assert costs.rates_for("M1", "droge_bulk").maintenance_eur_per_km == 204.0
    assert costs.fuel_provider.resolve("M1", "I", loading_ratio=1.0).fuel_rate_l_per_km == 2.0
    catalogue = load_vessel_catalogue(tmp_path / "reference")
    assert catalogue.navigability_vessel("M1", True).air_draught_m == 4.0


def test_build_shared_inputs_never_overwrites_or_guesses(
    tmp_path: Path, sources: ParameterSources
) -> None:
    parameters = tmp_path / "parameters"
    extract_parameter_tables(sources, parameters)
    target = tmp_path / "reference"
    target.mkdir()
    build_shared_inputs(parameters, target)  # an empty folder is fine
    with pytest.raises(ValidationError, match="already holds files"):
        build_shared_inputs(parameters, target)
    (parameters / "ce_delft_energy_use.csv").unlink()
    with pytest.raises(ValidationError, match="Parameter table not found.*ce_delft_energy_use"):
        build_shared_inputs(parameters, tmp_path / "second")
    assert not (tmp_path / "second").exists()
