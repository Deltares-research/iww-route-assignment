"""The synthetic assignment example runs end to end and shows the route choice it describes.

Network: A-B-D is 50 km with one lock, A-C-D is 80 km and faster. Three M8 trips: empty,
half and full. Needs no data files and runs in about a second.
"""

from __future__ import annotations

import math
import runpy
from pathlib import Path
from typing import Any

import pytest

from iww_route_assignment import RouteCostMetric, TripStatus

EXAMPLE = Path(__file__).parents[2] / "examples" / "assign_synthetic_trips.py"
TRIPS = ("empty", "half", "full")


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    return runpy.run_path(str(EXAMPLE))


def test_example_script_prints_a_route_per_trip(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    output = capsys.readouterr().out
    assert "travel_cost_eur" in output
    assert output.count("A-B-D") == 3


@pytest.mark.parametrize(
    ("metric", "lock_width_m", "expected"),
    [
        pytest.param(RouteCostMetric.TRAVEL_COST_EUR, 12.0, "A-B-D", id="cost-shorter-route"),
        pytest.param(RouteCostMetric.TRAVEL_TIME, 12.0, "A-C-D", id="time-faster-route"),
        pytest.param(RouteCostMetric.TRAVEL_COST_EUR, 11.0, "A-C-D", id="narrow-lock-avoided"),
    ],
)
def test_route_choice(
    example: dict[str, Any], metric: RouteCostMetric, lock_width_m: float, expected: str
) -> None:
    result = example["run"](metric, lock_width_m)
    assert (result.trips["status"] == TripStatus.SUCCESS.value).all()
    assert example["routes"](result).to_dict() == dict.fromkeys(TRIPS, expected)
    # The chosen route breaks no limit, so no penalty is paid.
    assert (result.trips["penalty_total_h"] == 0).all()


def test_costs_are_complete_and_follow_the_load(example: dict[str, Any]) -> None:
    trips = example["run"]().trips.set_index("trip_id").loc[list(TRIPS)]
    assert trips["distance_km"].tolist() == pytest.approx([50.0] * 3)
    for column in ("fuel_l", "fixed_cost_eur", "reported_total_cost_eur", "total_cost_eur"):
        assert all(math.isfinite(value) and value >= 0 for value in trips[column]), column
    # 9, 12 and 15 l/km for empty, half and full: more cargo burns more fuel.
    assert trips["fuel_l"].is_monotonic_increasing
    assert trips["fuel_l"]["empty"] < trips["fuel_l"]["full"]
    assert (trips["reported_total_cost_eur"] >= trips["fixed_cost_eur"]).all()
