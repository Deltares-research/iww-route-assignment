"""Non-blocking cost-batch benchmark with runtime and Python peak memory."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import tracemalloc

from iww_route_assignment import (
    ArcTravelInput,
    CostRates,
    FuelConsumptionObservation,
    LoadingCategory,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    calculate_arc_costs,
)


def run(row_count: int) -> dict[str, float | int | str]:
    """Run one synthetic batch and return reproducibility metadata."""
    provider = TableFuelConsumptionProvider(
        (
            FuelConsumptionObservation(
                "benchmark:M1:I",
                "M1",
                "I",
                37.0,
                50.0,
                58.0,
                "synthetic-benchmark",
            ),
        )
    )
    fuel = provider.resolve("M1", "I", loading_category=LoadingCategory.MEDIUM)
    rates = CostRates(
        "Dry bulk",
        "M1",
        28.0,
        25.0,
        8.0,
        1.5,
        3.0,
        1.2,
        12.0,
        2.0,
        4.0,
        1.0,
        1.2,
        "synthetic-benchmark",
    )
    scenario = ScenarioCostParameters(1.20, 3.0, 1.0)
    arc = ArcTravelInput(2.0, 10.0, lock_waiting_time_h=0.05)
    arcs = (arc,) * row_count
    fuels = (fuel,) * row_count

    tracemalloc.start()
    started = time.perf_counter()
    results = calculate_arc_costs(arcs, rates, scenario, fuels)
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(results) == row_count

    return {
        "rows": row_count,
        "elapsed_s": elapsed,
        "rows_per_s": row_count / elapsed,
        "python_peak_memory_mb": peak_bytes / 1_000_000,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    arguments = parser.parse_args()
    if arguments.rows <= 0:
        parser.error("--rows must be greater than zero")
    print(json.dumps(run(arguments.rows), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
