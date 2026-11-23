"""The front door: paths, config and one call, on a synthetic run written to files.

Nine source rows. Six become trips: a, b, c and h are routed, d has an unknown vessel class,
g has no cost rates. Row f is rejected for its cargo text. The two rows with id e are left out.

Hand calculation for trip a (M1, 175 t of 350 t, dry bulk) by cost, route A-B-D:
distance 20 km x 10 EUR/km = 200. Time 2 h sailing + 0.5 h lock = 2.5 h x 30 EUR/h = 75.
Fuel 6 l/km x 20 km + 6 l/km x 10 km/h x 0.1 x 0.5 h = 123 l x 2 EUR/l = 246.
Fixed (2 + 3) EUR/t x 175 t + 7 EUR/h x 2 h = 889. Total 1410.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pandas as pd
import pytest

from iww_route_assignment import (
    AssignmentConfig,
    AssignmentPaths,
    AssignmentSettings,
    DuplicateTripRule,
    FuelResolutionPolicy,
    IvsFormat,
    ValidationError,
    run_assignment,
)
from iww_route_assignment.io import (
    assign,
    filter_trips_by_period,
    largest_hand_difference_eur,
    load_assignment_inputs,
    load_cost_inputs,
    load_vessel_catalogue,
    module_check_tables,
    read_trip_frame,
    sha256_file,
)
from iww_route_assignment.io.workflow import REFERENCE_FILES

from .conftest import (
    WORKFLOW_SCENARIO,
    WorkflowFiles,
    workflow_config,
    workflow_paths,
    workflow_trip_rows,
)


def _ids(table: pd.DataFrame) -> list[str]:
    return [str(trip).rsplit(":", 1)[-1] for trip in table["trip_id"]]


def test_paths_and_config_are_frozen_and_validated(workflow_files: WorkflowFiles) -> None:
    paths = AssignmentPaths(
        trips=str(workflow_files.trips),  # type: ignore[arg-type]
        graph=str(workflow_files.graph),  # type: ignore[arg-type]
        reference=str(workflow_files.reference),  # type: ignore[arg-type]
    )
    assert isinstance(paths.trips, Path) and paths.structures is None
    with pytest.raises(FrozenInstanceError):
        paths.trips = Path("other.csv")  # type: ignore[misc]
    config = AssignmentConfig(
        scenario=WORKFLOW_SCENARIO,
        cargo_weight_unit="t",
        ivs_format="extended_2026",  # type: ignore[arg-type]
        duplicate_trip_ids="exclude",  # type: ignore[arg-type]
        fuel_policy="audited_proxy",  # type: ignore[arg-type]
    )
    assert config.ivs_format is IvsFormat.EXTENDED_2026
    assert config.duplicate_trip_ids is DuplicateTripRule.EXCLUDE
    assert config.fuel_policy is FuelResolutionPolicy.AUDITED_PROXY
    assert dict(config.class_aliases) == {"B01": "BO1", "B02": "BO2", "B04": "BO4"}
    with pytest.raises(FrozenInstanceError):
        config.sample_size = 3  # type: ignore[misc]
    with pytest.raises(ValidationError, match="sample_size must be positive"):
        replace(config, sample_size=0)
    with pytest.raises(ValueError, match="not a valid IvsFormat"):
        replace(config, ivs_format="spreadsheet")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AssignmentConfig()  # type: ignore[call-arg]  # scenario and cargo unit must be stated


def test_input_files_lists_only_files_that_exist(workflow_files: WorkflowFiles) -> None:
    files = workflow_paths(workflow_files).input_files()
    assert sorted(files) == [
        "cost_rates",
        "fuel_observations",
        "graph",
        "navigability_vessels",
        "structures_chambers",
        "structures_edges",
        "structures_openings",
        "trips",
        "vessel_references",
    ]
    (workflow_files.reference / "navigability_vessels.csv").unlink()
    without = AssignmentPaths(workflow_files.trips, workflow_files.graph, workflow_files.reference)
    assert sorted(without.input_files()) == [
        "cost_rates",
        "fuel_observations",
        "graph",
        "trips",
        "vessel_references",
    ]


def test_load_cost_inputs_reads_the_reference_folder(workflow_files: WorkflowFiles) -> None:
    costs = load_cost_inputs(workflow_files.reference, WORKFLOW_SCENARIO)
    assert costs.scenario is WORKFLOW_SCENARIO
    assert costs.rates_for("M1", "droge_bulk").maintenance_eur_per_km == 10.0
    assert costs.fuel_provider.resolve("M1", "I", loading_ratio=0.5).fuel_rate_l_per_km == 6.0
    for name in ("cost_rates", "fuel_observations", "vessel_references"):
        moved = workflow_files.reference / REFERENCE_FILES[name]
        kept = moved.with_suffix(".kept")
        moved.rename(kept)
        with pytest.raises(ValidationError, match=f"{name} not found"):
            load_cost_inputs(workflow_files.reference, WORKFLOW_SCENARIO)
        kept.rename(moved)


def test_load_vessel_catalogue_with_and_without_states(workflow_files: WorkflowFiles) -> None:
    catalogue = load_vessel_catalogue(workflow_files.reference, {"M9": "M1"})
    assert catalogue.resolve_class("M9") == "M1"
    assert catalogue.navigability_vessel("M1", True).air_draught_m == 4.0
    assert catalogue.navigability_vessel("M1", False).draught_m == 1.0
    (workflow_files.reference / "navigability_vessels.csv").unlink()
    plain = load_vessel_catalogue(workflow_files.reference)
    assert plain.resolve_class("M9") is None
    # Without the states file the reference dimensions are used, without an air draught.
    assert plain.navigability_vessel("M1", True).air_draught_m is None
    assert plain.navigability_vessel("M1", True).draught_m == 2.2
    (workflow_files.reference / "vessel_references.csv").unlink()
    with pytest.raises(ValidationError, match="vessel_references not found"):
        load_vessel_catalogue(workflow_files.reference)


def test_period_filter_start_is_inclusive_and_end_is_exclusive() -> None:
    frame = workflow_trip_rows()
    assert filter_trips_by_period(frame, None, None) is frame
    march = filter_trips_by_period(frame, "2026-03-01", "2026-04-01")
    # h starts on 27 February. c starts exactly at the end bound and is left out.
    assert march["Pseudo_ReisID"].tolist() == ["a", "b", "d", "e", "e", "f", "g"]
    exact = filter_trips_by_period(frame, "2026-03-01 08:00:00", "2026-03-10 09:30:00")
    assert exact["Pseudo_ReisID"].tolist() == ["a"]
    assert filter_trips_by_period(frame, "2026-04-01", None)["Pseudo_ReisID"].tolist() == ["c"]
    assert filter_trips_by_period(frame, None, "2026-03-01")["Pseudo_ReisID"].tolist() == ["h"]
    assert frame.shape == workflow_trip_rows().shape  # the input frame is not changed


def test_period_filter_rejects_empty_periods_and_bad_input() -> None:
    frame = workflow_trip_rows()
    with pytest.raises(ValidationError, match="No trips start between 2027-01-01 and None"):
        filter_trips_by_period(frame, "2027-01-01", None)
    with pytest.raises(ValidationError, match="Period bounds must be dates"):
        filter_trips_by_period(frame, "next spring", None)
    with pytest.raises(ValidationError, match="Period bounds must be dates"):
        filter_trips_by_period(frame, None, "NaT")
    with pytest.raises(ValidationError, match="needs column 'Vertrek'"):
        filter_trips_by_period(frame, "2026-03-01", None, column="Vertrek")
    unreadable = frame.assign(Eerste_Tijdstip=frame["Eerste_Tijdstip"].where(frame.index != 1, "?"))
    kept = filter_trips_by_period(unreadable, "2026-01-01", None)
    assert "b" not in kept["Pseudo_ReisID"].tolist() and len(kept) == len(frame) - 1


def test_trip_frame_is_read_as_text(tmp_path: Path, workflow_files: WorkflowFiles) -> None:
    frame = read_trip_frame(workflow_files.trips)
    assert all(str(dtype) == "string" for dtype in frame.dtypes)
    assert frame["Jaar"].iloc[0] == "2026"
    parquet = tmp_path / "trips.parquet"
    frame.astype(object).to_parquet(parquet)
    pd.testing.assert_frame_equal(read_trip_frame(parquet), frame)
    with pytest.raises(ValidationError, match="trips not found"):
        read_trip_frame(tmp_path / "missing.csv")


def test_run_assignment_writes_every_output(tmp_path: Path, workflow_files: WorkflowFiles) -> None:
    paths = workflow_paths(workflow_files)
    run = run_assignment(paths, workflow_config(), tmp_path / "out")
    assert sorted(run.output_files) == [
        "gpkg",
        "intensity",
        "map",
        "rejections",
        "run_metadata",
        "summary",
        "trip_edges",
        "trips",
    ]
    assert all(path.is_file() for path in run.output_files.values())
    assert run.output_files["map"].name == "assignment_map.html"

    trips = run.result.trips.set_index(pd.Index(_ids(run.result.trips)))
    assert trips["status"].to_dict() == {
        "a": "success",
        "b": "success",
        "c": "success",
        "d": "vessel_fail",
        "g": "cost_fail",
        "h": "success",
    }
    assert run.result.summary["excluded_duplicate_rows"] == 2
    assert [r.source_row_id for r in run.result.rejections] == ["ivs_trips_test:2026:f"]
    assert trips.loc["a", "reported_total_cost_eur"] == pytest.approx(1410.0)
    assert trips.loc["a", "departure_time"] == "2026-03-01 08:00:00"
    stored = pd.read_parquet(run.output_files["trips"])
    assert stored["reported_total_cost_eur"].iloc[0] == pytest.approx(1410.0)
    assert run.config.scenario is WORKFLOW_SCENARIO
    assert run.inputs.network_report["restrictions_by_type"] == {"lock": 1, "waterway": 4}
    assert len(run.inputs.source_frame) == 9


def test_run_metadata_holds_settings_and_checksums_without_folders(
    tmp_path: Path, workflow_files: WorkflowFiles
) -> None:
    paths = workflow_paths(workflow_files)
    run = run_assignment(paths, workflow_config(), tmp_path / "out", write_map=False)
    text = run.output_files["run_metadata"].read_text(encoding="utf-8")
    block = json.loads(text)
    assert block["input_files"]["trips"] == "ivs_trips_test.csv"
    assert block["input_files"]["structures_chambers"] == "chambers.geoparquet"
    assert block["input_sha256"]["trips"] == sha256_file(workflow_files.trips)
    assert block["input_sha256"]["cost_rates"] == sha256_file(
        workflow_files.reference / "cost_rates.csv"
    )
    assert block["config"]["scenario"]["fuel_price_eur_per_l"] == 2.0
    assert block["config"]["cargo_weight_unit"] == "t"
    assert block["config"]["settings"]["metric"] == "travel_cost_eur"
    assert block["config"]["class_aliases"]["B04"] == "BO4"
    assert block["summary"]["status_counts"] == {"cost_fail": 1, "success": 4, "vessel_fail": 1}
    assert block["network_report"]["rules"]["lock_passage_time_h"] == 0.5
    assert block["network_report"]["edges_with_locks"] == 1
    assert tmp_path.name not in text and tmp_path.as_posix() not in text


def test_map_and_files_are_optional(tmp_path: Path, workflow_files: WorkflowFiles) -> None:
    paths = workflow_paths(workflow_files)
    quiet = run_assignment(paths, workflow_config(), tmp_path / "out", write_map=False)
    assert "map" not in quiet.output_files
    assert not (tmp_path / "out" / "assignment_map.html").exists()
    memory = run_assignment(paths, workflow_config())
    assert dict(memory.output_files) == {}
    pd.testing.assert_frame_equal(memory.result.trips, quiet.result.trips)


def test_one_call_equals_the_separate_steps(workflow_files: WorkflowFiles) -> None:
    paths = workflow_paths(workflow_files)
    config = workflow_config("travel_time")
    inputs = load_assignment_inputs(paths, config)
    stepwise = assign(inputs, config)
    whole = run_assignment(paths, config).result
    pd.testing.assert_frame_equal(stepwise.trips, whole.trips)
    pd.testing.assert_frame_equal(stepwise.trip_edges, whole.trip_edges)
    # By time every routed trip sails A-C-D: 30 km in 1.5 h, no lock.
    routed = whole.trips[whole.trips["status"] == "success"]
    assert routed["reported_time_h"].tolist() == [1.5] * 4
    assert routed["lock_passages"].tolist() == [0] * 4


def test_period_and_label_are_applied_before_reading(workflow_files: WorkflowFiles) -> None:
    config = workflow_config(
        period_start="2026-03-01", period_end="2026-04-01", dataset_label="register"
    )
    run = run_assignment(workflow_paths(workflow_files), config)
    assert _ids(run.result.trips) == ["a", "b", "d", "g"]
    assert run.result.trips["trip_id"].iloc[0] == "register:2026:a"
    with pytest.raises(ValidationError, match="No trips start"):
        run_assignment(workflow_paths(workflow_files), replace(config, period_start="2030-01-01"))


def test_sample_is_deterministic_for_a_seed(workflow_files: WorkflowFiles) -> None:
    paths = workflow_paths(workflow_files)
    first = load_assignment_inputs(paths, workflow_config(sample_size=4, sample_seed=7))
    again = load_assignment_inputs(paths, workflow_config(sample_size=4, sample_seed=7))
    assert len(first.source_frame) == 4
    pd.testing.assert_frame_equal(first.source_frame, again.source_frame)
    assert [t.source_row_id for t in first.trips.trips] == [
        t.source_row_id for t in again.trips.trips
    ]
    samples = {
        tuple(
            load_assignment_inputs(
                paths, workflow_config(sample_size=4, sample_seed=seed)
            ).source_frame.index
        )
        for seed in range(1, 6)
    }
    assert len(samples) > 1  # the seed selects the sample
    everything = load_assignment_inputs(paths, workflow_config(sample_size=50))
    assert len(everything.source_frame) == 9


def test_departure_column_can_be_switched_off(workflow_files: WorkflowFiles) -> None:
    settings = AssignmentSettings(points_crs="EPSG:4326", departure_column=None)
    config = replace(workflow_config(), settings=settings)
    run = run_assignment(workflow_paths(workflow_files), config)
    assert set(run.result.trips["departure_time"]) == {""}
    # The period filter still uses the standard start-time column.
    march = replace(config, period_start="2026-04-01")
    assert _ids(run_assignment(workflow_paths(workflow_files), march).result.trips) == ["c"]


def test_missing_inputs_stop_the_run_with_a_clear_message(
    tmp_path: Path, workflow_files: WorkflowFiles
) -> None:
    config = workflow_config()
    paths = workflow_paths(workflow_files)
    with pytest.raises(ValidationError, match="trips not found"):
        run_assignment(replace(paths, trips=tmp_path / "missing.csv"), config)
    with pytest.raises(ValidationError, match="graph not found"):
        run_assignment(replace(paths, graph=tmp_path / "missing.pickle"), config)
    (workflow_files.reference / "cost_rates.csv").unlink()
    with pytest.raises(ValidationError, match="cost_rates not found"):
        run_assignment(paths, config, tmp_path / "out")
    assert not (tmp_path / "out").exists()  # nothing is written when a run stops


def test_network_without_structure_files_still_runs(workflow_files: WorkflowFiles) -> None:
    paths = replace(workflow_paths(workflow_files), structures=None)
    run = run_assignment(paths, workflow_config())
    # Without the lock table there is no lock time: A-B-D takes 2 h.
    assert run.result.trips["reported_time_h"].iloc[0] == pytest.approx(2.0)
    assert run.result.trips["lock_passages"].iloc[0] == 0


def test_run_without_any_routed_trip_still_writes_outputs(
    tmp_path: Path, workflow_files: WorkflowFiles
) -> None:
    # Only trip d starts on 20 March, and its vessel class is unknown.
    config = workflow_config(period_start="2026-03-20", period_end="2026-03-21")
    run = run_assignment(workflow_paths(workflow_files), config, tmp_path / "out")
    assert run.result.summary["status_counts"] == {"vessel_fail": 1}
    assert run.result.trips["reported_total_cost_eur"].isna().all()
    assert run.result.trip_edges.empty and run.result.intensity.empty
    assert run.output_files["map"].is_file()
    assert pd.read_parquet(run.output_files["trips"])["status"].tolist() == ["vessel_fail"]
    rates = pd.read_csv(workflow_files.reference / "cost_rates.csv")
    tables = module_check_tables(run, rates)
    assert tables["4_cost"].empty and tables["3_dimension_control"].empty
    assert largest_hand_difference_eur(tables) == 0.0
