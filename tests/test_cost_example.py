"""The arc cost example runs and reports a complete trip cost."""

from __future__ import annotations

import json
import math
import runpy
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parents[1] / "examples" / "calculate_arc_cost.py"


def test_arc_cost_example_reports_a_complete_trip_cost(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    trip = json.loads(capsys.readouterr().out)["trip"]
    assert math.isfinite(trip["total_cost_eur"])
    assert trip["total_cost_eur"] > trip["fixed_cost_eur"] > 0
