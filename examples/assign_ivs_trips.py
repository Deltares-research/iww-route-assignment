"""Assign IVS voyages to the EURIS waterway graph with ``assign_new.assign_traffic``.

Inputs:

* IVS voyages: ``ivs_trips_extended_2026.csv`` (one voyage per ``Pseudo_ReisID``).
* Graph, lock/bridge data and ship-type database from the sibling ``fis-crawler`` project.
* Cost rates, fuel observations and vessel references: no real tables exist yet, so
  this script writes MADE-UP placeholder tables. Reported costs and fuel are therefore
  not meaningful; routes and travel times are.

Usage::

    uv run python examples/assign_ivs_trips.py                      # fastest routes
    uv run python examples/assign_ivs_trips.py --metric travel_cost_eur
    uv run python examples/assign_ivs_trips.py --sample 2000        # quick test
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from iww_route_assignment.assign_new import assign_traffic
from iww_route_assignment.domain.classifications import (
    load_shiptypes,
    normalise_cemt_class,
    normalise_rws_class,
    normalize_class_code,
)
from iww_route_assignment.domain.models import ScenarioCostParameters
from iww_route_assignment.exceptions import ValidationError

REPO = Path(__file__).resolve().parents[1]
FIS_CRAWLER = REPO.parent / "fis-crawler"

TRIPS_CSV = Path(
    "/Users/hemert/data/ivs/iww-route-assignment/ivs_trips_extended_2026.csv"
)
GRAPH_PATH = FIS_CRAWLER / "output" / "euris-enriched" / "graph.pickle"
EURIS_STRUCTURES_DIR = FIS_CRAWLER / "output" / "dropins-euris-detailed"
REFERENCE_DIR = FIS_CRAWLER / "reference"  # holds DTV_shiptypes_database.json
OUTPUT_DIR = REPO / "output" / "assign_ivs_trips"

APPEARANCES = ("droge_bulk", "natte_bulk", "container", "overig")
WATERWAY_CLASSES = ("I", "II", "III", "IV", "Va", "Vb", "VIa", "VIb", "VIc", "VII")
PLACEHOLDER_SOURCE = "made-up-placeholder"


def write_placeholder_tables(
    vessel_classes: list[str], reference_dir: Path, target_dir: Path
) -> tuple[Path, Path, Path]:
    """Write made-up cost, fuel and vessel-reference tables for every vessel class.

    Replace these with real tables once available.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    ship_db = load_shiptypes(reference_dir)
    costs, fuel, references = [], [], []
    for i, vessel_class in enumerate(vessel_classes):
        for appearance in APPEARANCES:
            costs.append(
                {
                    "appearance": appearance,
                    "rws_class": vessel_class,
                    "labor_h": 50,
                    "labor_wait_h": 40,
                    "maint_h": 10,
                    "maint_km": 1,
                    "insurance_h": 5,
                    "interest_pct_h": 1,
                    "depreciation_h": 20,
                    "harbor_h": 5,
                    "other_h": 5,
                    "load_t": 2,
                    "unload_t": 2,
                    "source": PLACEHOLDER_SOURCE,
                }
            )
        for waterway_class in WATERWAY_CLASSES:
            for profile in ("general", "container"):
                fuel.append(
                    {
                        "source_row_id": f"{profile}:{vessel_class}:{waterway_class}",
                        "vessel_rws_class": vessel_class,
                        "waterway_cemt_class": waterway_class,
                        "vessel_profile": profile,
                        "diesel_light_l_per_h": 40 + i,
                        "diesel_medium_l_per_h": 60 + i,
                        "diesel_heavy_l_per_h": 80 + i,
                        "source_dataset": PLACEHOLDER_SOURCE,
                    }
                )
        ship = ship_db.get(normalize_class_code(vessel_class), {})
        try:
            cemt = normalise_cemt_class(str(ship.get("CEMT", "Va")))
        except ValidationError:
            cemt = "Va"
        references.append(
            {
                "rws_class": vessel_class,
                "cemt_class": cemt,
                "capacity_t": float(ship.get("Capacity [t]") or 1000.0),
                "beam_m": float(ship.get("Beam [m]", 11.4)),
                "length_m": float(ship.get("Length [m]", 110.0)),
                "draught_loaded_m": 3.0,
                "draught_empty_m": 1.2,
            }
        )

    paths = (
        target_dir / "cost_rates.csv",
        target_dir / "fuel_observations.csv",
        target_dir / "vessel_references.csv",
    )
    for path, rows in zip(paths, (costs, fuel, references), strict=True):
        pd.DataFrame(rows).to_csv(path, index=False)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--metric", choices=("travel_time", "travel_cost_eur"), default="travel_time"
    )
    parser.add_argument(
        "--sample", type=int, default=None, help="route a random sample only"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    trips = pd.read_csv(TRIPS_CSV, index_col=0)
    if args.sample:
        trips = trips.sample(args.sample, random_state=1)

    vessel_classes = sorted(
        {
            normalise_rws_class(c)
            for c in trips["SK_CODE"].dropna().astype(str)
            if c.strip()
        }
    )
    cost_rates, fuel_observations, vessel_references = write_placeholder_tables(
        vessel_classes, REFERENCE_DIR, args.output_dir / "placeholder_inputs"
    )

    start = time.perf_counter()
    result = assign_traffic(
        trips,
        GRAPH_PATH,
        args.output_dir / args.metric,
        cost_rates_path=cost_rates,
        fuel_observations_path=fuel_observations,
        vessel_reference_path=vessel_references,
        scenario=ScenarioCostParameters(
            fuel_price_eur_per_l=1.0,
            interest_rate_percent=4.0,
            cargo_waiting_time_h=0.0,
        ),
        metric=args.metric,
        reference_dir=REFERENCE_DIR,
        euris_structures_dir=EURIS_STRUCTURES_DIR,
        # Round the loading ratio used for route choice so voyages can share searches;
        # reported costs still use each voyage's exact load.
        routing_ratio_step=0.05 if args.metric == "travel_cost_eur" else None,
        n_workers=args.workers,
    )
    elapsed = time.perf_counter() - start

    voyages = result.voyages
    routed = voyages[voyages["status"] == "success"]
    print(f"\nAssigned {len(voyages)} voyages in {elapsed:.0f} s ({args.metric}).")
    print(voyages["status"].value_counts().to_string())
    print(f"Mean route length: {routed['distance_km'].mean():.1f} km")
    print(f"Mean travel time:  {routed['reported_time_h'].mean():.2f} h")
    print(f"Outputs written to {args.output_dir / args.metric}")


if __name__ == "__main__":
    main()
