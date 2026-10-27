"""The IDE example runs and shows the route choice it describes."""

from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLE = Path(__file__).parents[2] / "examples" / "assign_synthetic_trips.py"


def test_example_routes_by_cost_via_b(capsys) -> None:
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    output = capsys.readouterr().out
    assert "travel_cost_eur" in output
    assert output.count("A-B-D") == 3
