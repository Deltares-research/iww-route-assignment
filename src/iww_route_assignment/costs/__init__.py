"""Public monetary-cost and fuel-resolution API."""

from .calculator import (
    ArcCostArrays,
    calculate_arc_choice_time_h,
    calculate_arc_cost,
    calculate_arc_cost_arrays,
    calculate_arc_costs,
    calculate_trip_fixed_cost,
    combine_trip_cost,
    combine_trip_cost_arrays,
    hourly_rate_eur_per_h,
)
from .fuel import (
    FallbackFuelConsumptionProvider,
    FuelConsumptionProvider,
    TableFuelConsumptionProvider,
)

__all__ = [
    "ArcCostArrays",
    "calculate_arc_cost_arrays",
    "combine_trip_cost_arrays",
    "hourly_rate_eur_per_h",
    "FallbackFuelConsumptionProvider",
    "FuelConsumptionProvider",
    "TableFuelConsumptionProvider",
    "calculate_arc_choice_time_h",
    "calculate_arc_cost",
    "calculate_arc_costs",
    "calculate_trip_fixed_cost",
    "combine_trip_cost",
]
