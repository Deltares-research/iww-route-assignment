from pathlib import Path

import pytest

from iww_route_assignment import (
    CostRates,
    FuelResolutionPolicy,
    ScenarioCostParameters,
    TableFuelConsumptionProvider,
)
from iww_route_assignment.io import (
    load_fuel_observations_csv,
    load_vessel_references_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cost_rates() -> CostRates:
    return CostRates(
        appearance="Dry bulk",
        vessel_rws_class="M1",
        labour_eur_per_h=10.0,
        labour_waiting_eur_per_h=7.0,
        maintenance_eur_per_h=2.0,
        maintenance_eur_per_km=3.0,
        insurance_eur_per_h=1.0,
        interest_eur_per_percent_h=0.5,
        depreciation_eur_per_h=4.0,
        port_dues_eur_per_h=5.0,
        other_eur_per_h=6.0,
        loading_eur_per_t=2.0,
        unloading_eur_per_t=3.0,
        source="synthetic-test",
    )


@pytest.fixture
def scenario() -> ScenarioCostParameters:
    return ScenarioCostParameters(
        fuel_price_eur_per_l=2.0,
        interest_rate_percent=4.0,
        cargo_waiting_time_h=2.0,
        idle_fuel_factor=0.1,
    )


@pytest.fixture
def fuel_provider() -> TableFuelConsumptionProvider:
    return TableFuelConsumptionProvider(
        load_fuel_observations_csv(FIXTURES / "fuel_observations.csv"),
        load_vessel_references_csv(FIXTURES / "vessel_references.csv"),
        policy=FuelResolutionPolicy.AUDITED_PROXY,
    )
