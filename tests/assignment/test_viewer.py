"""The interactive map: the data behind it and the HTML file.

Four routed trips sail A-B-D by cost: cargo 175 + 0 + 100 + 350 = 625 t, one of four empty.
Section A-B: cost 250 + 210 + 232.86 + 290 = 982.86 EUR, fuel 60 + 40 + 51.43 + 80 = 231.43 l.
Trip totals: 1410 + 439 + 999.86 + 2367 = 5215.86 EUR, fuel 123 + 82 + 105.43 + 164 = 474.43 l.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iww_route_assignment import AssignmentRun, assign_traffic, run_assignment
from iww_route_assignment.io import assignment_map_data, write_assignment_map
from iww_route_assignment.io.viewer import MEASURES, PENALTY_KEYS, TEMPLATE_FILE

from .conftest import (
    WorkflowFiles,
    make_network,
    make_trip,
    make_trips,
    workflow_config,
    workflow_paths,
)


@pytest.fixture
def by_cost(workflow_files: WorkflowFiles) -> AssignmentRun:
    return run_assignment(workflow_paths(workflow_files), workflow_config())


@pytest.fixture
def by_time(workflow_files: WorkflowFiles) -> AssignmentRun:
    return run_assignment(workflow_paths(workflow_files), workflow_config("travel_time"))


def test_map_data_lists_measures_and_penalty_types(by_cost: AssignmentRun) -> None:
    data = assignment_map_data(by_cost.result, by_cost.inputs.network, title="Case")
    assert data["title"] == "Case"
    assert [m["key"] for m in data["measures"]] == [key for key, _, _ in MEASURES]
    assert {"key": "fuel_l", "label": "Fuel", "unit": "l"} in data["measures"]
    assert data["penalty_types"] == list(PENALTY_KEYS)
    assert data["other_label"] == ""
    json.dumps(data)  # everything is JSON-ready


def test_map_summary_matches_the_trip_table(by_cost: AssignmentRun) -> None:
    summary = assignment_map_data(by_cost.result, by_cost.inputs.network)["summary"]
    assert summary == {
        "trips": 6,
        "routed": 4,
        "status": {"cost_fail": 1, "success": 4, "vessel_fail": 1},
        "rejected": 1,
        "cost_eur": 5216.0,
        "fuel_l": 474.0,
        "km": 80.0,
        "penalised_trips": 3,
        "fuel_proxy_share": 0.0,
        "metric": "travel_cost_eur",
    }


def test_used_sections_carry_values_and_unmet_limits(by_cost: AssignmentRun) -> None:
    data = assignment_map_data(by_cost.result, by_cost.inputs.network, simplify_tolerance_deg=0.0)
    edges = {edge["i"]: edge for edge in data["edges"]}
    assert len(edges) == 4
    assert [edge["u"] for edge in data["edges"]] == [1, 0, 1, 0]  # A-B, A-C, B-D, C-D
    first = edges[0]
    assert (first["name"], first["code"], first["cemt"]) == ("NL01", "NL01", "I")
    assert (first["km"], first["locks"]) == (10.0, 0)
    assert first["v"] == {
        "trips": 4,
        "cargo_t": 625.0,
        "cost_eur": 983.0,
        "fuel_l": 231.0,
        "time_h": 4.0,
        "empty_share": 25.0,
        "penalty_h": 0.0,
    }
    assert first["p"] == []
    # The graph has no line geometry, so the section is drawn between its nodes, as lat, lon.
    assert first["g"] == [[52.0, 5.0], [52.05, 5.1]]
    lock_section = edges[2]
    assert lock_section["locks"] == 1
    assert lock_section["p"] == [
        {
            "type": "draught",
            "structure": "NLLOCK1",
            "structure_type": "lock",
            "limit_m": 2.0,
            "penalty_h": 0.0,
            "trips": 3,
            "classes": "M1",
        }
    ]
    assert set(edges[1]) == {"i", "g", "u"}  # an unused section has geometry only


def test_unused_network_can_be_left_out(by_cost: AssignmentRun) -> None:
    data = assignment_map_data(by_cost.result, by_cost.inputs.network, include_unused=False)
    assert [edge["i"] for edge in data["edges"]] == [0, 2]
    assert all(edge["u"] == 1 for edge in data["edges"])


def test_trips_are_listed_with_the_second_result_beside_them(
    by_cost: AssignmentRun, by_time: AssignmentRun
) -> None:
    data = assignment_map_data(by_cost.result, by_cost.inputs.network, other=by_time.result)
    assert data["other_label"] == "travel_time"
    trips = {trip["id"]: trip for trip in data["trips"]}
    assert list(trips) == ["a", "b", "c", "d", "g", "h"]
    a = trips["a"]
    assert (a["status"], a["cls"], a["app"], a["cargo_t"]) == ("success", "M1", "droge_bulk", 175.0)
    assert a["e"] == [0, 2]
    assert (a["km"], a["h"], a["fuel_l"]) == (20.0, 2.5, 123.0)
    assert (a["distance_cost"], a["time_cost"], a["fuel_cost"]) == (200.0, 75.0, 246.0)
    assert (a["fixed_cost"], a["cost"], a["confidence"]) == (889.0, 1410.0, "high")
    assert a["alt"] == {"e": [1, 3], "km": 30.0, "h": 1.5, "cost": 1594.0}
    failed = trips["d"]
    assert (failed["status"], failed["cls"], failed["e"]) == ("vessel_fail", "", [])
    assert "no vessel reference" in failed["detail"]
    assert "cost" not in failed and "alt" not in failed


def test_trip_list_is_dropped_above_the_cap(by_cost: AssignmentRun, by_time: AssignmentRun) -> None:
    network = by_cost.inputs.network
    capped = assignment_map_data(by_cost.result, network, other=by_time.result, max_trips=5)
    assert capped["trips"] == [] and capped["other_label"] == ""
    assert len(capped["edges"]) == 4  # the sections are still drawn
    at_cap = assignment_map_data(by_cost.result, network, max_trips=6)
    assert len(at_cap["trips"]) == 6


def test_map_of_a_run_without_routed_trips(cost_inputs, catalogue) -> None:
    network = make_network()
    result = assign_traffic(
        make_trips([make_trip("unknown", 0.0, vessel="B04")]), network, cost_inputs, catalogue
    )
    data = assignment_map_data(result, network)
    assert data["summary"]["routed"] == 0
    assert data["summary"]["fuel_proxy_share"] == 0.0
    assert data["summary"]["cost_eur"] == 0.0
    assert [edge["u"] for edge in data["edges"]] == [0, 0, 0, 0]
    assert data["trips"][0]["status"] == "vessel_fail"


def test_write_map_embeds_the_data_in_one_html_file(
    tmp_path: Path, by_cost: AssignmentRun, by_time: AssignmentRun
) -> None:
    target = write_assignment_map(
        by_cost.result,
        by_cost.inputs.network,
        tmp_path / "new" / "map.html",
        title="Case <1>",
        other=by_time.result,
    )
    assert target == tmp_path / "new" / "map.html"
    assert [path.name for path in target.parent.iterdir()] == ["map.html"]
    html = target.read_text(encoding="utf-8")
    assert "__DATA__" not in html and "__TITLE__" not in html
    assert "<title>Case &lt;1></title>" in html
    line = next(row for row in html.splitlines() if row.startswith("const DATA = "))
    embedded = json.loads(
        line.removeprefix("const DATA = ").removesuffix(";").replace("<\\/", "</")
    )
    expected = assignment_map_data(
        by_cost.result, by_cost.inputs.network, title="Case <1>", other=by_time.result
    )
    assert embedded == expected
    assert "leaflet" in html.lower()


def test_embedded_text_cannot_close_the_script_block(
    tmp_path: Path, by_cost: AssignmentRun
) -> None:
    target = write_assignment_map(
        by_cost.result, by_cost.inputs.network, tmp_path / "map.html", title="a</script>b"
    )
    html = target.read_text(encoding="utf-8")
    assert "a</script>b" not in html
    assert "a<\\/script>b" in html
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    assert html.count("</script>") == template.count("</script>")


def test_trip_labels_name_and_order_the_trip_list(by_cost: AssignmentRun) -> None:
    result = by_cost.result
    full_id = str(result.trips["trip_id"].iloc[-1])
    short_id = str(result.trips["trip_id"].iloc[0]).rsplit(":", 1)[-1]
    labels = {full_id: "T02", short_id: "T01"}
    data = assignment_map_data(result, by_cost.inputs.network, trip_labels=labels)
    listed = [trip["label"] for trip in data["trips"]]
    # Labelled trips come first, in label order. A full id and a short id both match.
    assert listed[:2] == ["T01", "T02"]
    assert set(listed[2:]) <= {""}
    plain = assignment_map_data(result, by_cost.inputs.network)
    assert all(trip["label"] == "" for trip in plain["trips"])
    assert len(plain["trips"]) == len(data["trips"])
