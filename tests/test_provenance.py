"""Checksums, JSON-ready settings and the provenance block of a run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from iww_route_assignment import AssignmentSettings, RouteCostMetric
from iww_route_assignment.io import run_metadata, sha256_file, to_record, write_json


@dataclass(frozen=True)
class _Example:
    metric: RouteCostMetric
    folder: Path
    names: frozenset[str]
    derived: int = field(init=False, default=7)


def test_sha256_matches_hashlib_and_reads_in_chunks(tmp_path: Path) -> None:
    small = tmp_path / "small.txt"
    small.write_bytes(b"abc")
    # SHA-256 of "abc", the standard test vector.
    assert sha256_file(small) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    large = tmp_path / "large.bin"
    content = b"x" * ((1 << 20) + 5)  # one byte chunk boundary is crossed
    large.write_bytes(content)
    assert sha256_file(large) == hashlib.sha256(content).hexdigest()


def test_to_record_turns_settings_into_plain_values() -> None:
    record = to_record(
        {
            1: _Example(RouteCostMetric.TRAVEL_TIME, Path("a") / "b", frozenset({"z", "y"})),
            "tuple": (1, RouteCostMetric.TRAVEL_COST_EUR),
            "set": {3, 1, 2},
        }
    )
    # Fields that are not constructor arguments are left out. Sets are sorted.
    assert record == {
        "1": {"metric": "travel_time", "folder": "a/b", "names": ["y", "z"]},
        "tuple": [1, "travel_cost_eur"],
        "set": [1, 2, 3],
    }
    assert to_record(_Example) is _Example  # a class, not an instance, is left alone
    assert to_record(AssignmentSettings())["metric"] == "travel_cost_eur"


def test_run_metadata_records_names_and_checksums_not_folders(tmp_path: Path) -> None:
    folder = tmp_path / "private user folder"
    folder.mkdir()
    trips = folder / "trips.csv"
    trips.write_text("a,b\n1,2\n", encoding="utf-8")
    graph = folder / "graph.pickle"
    graph.write_bytes(b"graph")
    block = run_metadata(
        {"trips": trips, "graph": str(graph)},
        settings=AssignmentSettings(n_workers=2),
        rules=["rule-1"],
    )
    assert block["input_files"] == {"graph": "graph.pickle", "trips": "trips.csv"}
    assert block["input_sha256"] == {
        "graph": hashlib.sha256(b"graph").hexdigest(),
        "trips": sha256_file(trips),
    }
    assert block["settings"]["n_workers"] == 2
    assert block["rules"] == ["rule-1"]
    assert {"generated_at", "package_version", "python", "platform"} <= set(block)
    assert "private user folder" not in json.dumps(block)


def test_run_metadata_does_not_depend_on_input_order(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    forward = run_metadata({"x": first, "y": second})
    backward = run_metadata({"y": second, "x": first})
    assert list(forward["input_sha256"]) == list(backward["input_sha256"]) == ["x", "y"]
    assert forward["input_sha256"] == backward["input_sha256"]


def test_write_json_is_sorted_indented_and_creates_folders(tmp_path: Path) -> None:
    target = write_json(
        tmp_path / "new" / "folder" / "report.json",
        {"b": RouteCostMetric.TRAVEL_TIME, "a": Path("x") / "y", "c": {2, 1}},
    )
    text = target.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert text.index('"a"') < text.index('"b"') < text.index('"c"')
    assert json.loads(text) == {"a": "x/y", "b": "travel_time", "c": [1, 2]}
