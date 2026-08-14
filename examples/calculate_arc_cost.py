"""Run a synthetic, auditable arc and trip cost calculation."""

from __future__ import annotations

import json
from dataclasses import asdict

from iww_route_assignment import (
    ArcTravelInput,
    CostRates,
    FuelConsumptionObservation,
    LoadingCategory,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
    TripCostInput,
    VesselProfile,
    VesselReference,
    calculate_arc_cost,
    calculate_trip_fixed_cost,
    combine_trip_cost,
)
from iww_route_assignment.io import arc_cost_to_record


def main() -> None:
    """Calculate and print one complete synthetic result."""
    provider = TableFuelConsumptionProvider(
        observations=(
            FuelConsumptionObservation(
                source_row_id="example:M1:I",
                vessel_rws_class="M1",
                waterway_cemt_class="I",
                vessel_profile=VesselProfile.GENERAL,
                diesel_light_l_per_h=37.0,
                diesel_medium_l_per_h=50.0,
                diesel_heavy_l_per_h=58.0,
                source_dataset="synthetic-example",
            ),
        ),
        vessel_references=(VesselReference("M1", "I", 350.0, 5.05, 38.5, 2.2, 1.0),),
    )
    fuel = provider.resolve(
        "M1",
        "I",
        VesselProfile.GENERAL,
        loading_category=LoadingCategory.MEDIUM,
    )
    rates = CostRates(
        appearance="Dry bulk",
        vessel_rws_class="M1",
        labour_eur_per_h=28.0,
        labour_waiting_eur_per_h=25.0,
        maintenance_eur_per_h=8.0,
        maintenance_eur_per_km=1.5,
        insurance_eur_per_h=3.0,
        interest_eur_per_percent_h=1.2,
        depreciation_eur_per_h=12.0,
        port_dues_eur_per_h=2.0,
        other_eur_per_h=4.0,
        loading_eur_per_t=1.0,
        unloading_eur_per_t=1.2,
        source="synthetic-example",
    )
    scenario = ScenarioCostParameters(
        fuel_price_eur_per_l=1.20,
        interest_rate_percent=3.0,
        cargo_waiting_time_h=1.0,
        idle_fuel_factor=0.15,
    )
    arc = calculate_arc_cost(
        ArcTravelInput(
            distance_km=20.0,
            realised_speed_km_per_h=10.0,
            lock_service_time_h=0.25,
            lock_waiting_time_h=0.10,
        ),
        rates,
        scenario,
        fuel,
    )
    fixed = calculate_trip_fixed_cost(TripCostInput(cargo_t=200.0, is_loaded=True), rates, scenario)
    trip = combine_trip_cost((arc,), fixed)

    output = {
        "arc": arc_cost_to_record("synthetic-arc", arc),
        "fixed_trip_cost": asdict(fixed),
        "trip": asdict(trip),
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
