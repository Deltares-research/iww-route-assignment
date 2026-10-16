"""Derive arc-cost requests from a synthetic trip register in the IVS layout.

The example uses the synthetic fixtures in ``tests/fixtures``. It shows the
derivation of vessel class, cargo appearance, loading ratio and route
attributes, and it prints the coverage summary that a case run records. No
production or licensed data is read.
"""

from __future__ import annotations

import json
from pathlib import Path

from iww_route_assignment import (
    build_route_index,
    summarise_trip_conversion,
    trips_to_arc_requests,
)
from iww_route_assignment.io import (
    arc_requests_to_frame,
    load_appearance_map_csv,
    load_ivs_trips_csv,
    load_route_profiles_csv,
    load_vessel_type_map_csv,
    trip_rejections_to_frame,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> None:
    """Prepare requests from the synthetic fixture and print the audit summary."""
    trips = load_ivs_trips_csv(FIXTURES / "ivs_trips.csv", source_dataset="synthetic-example")
    routes = build_route_index(load_route_profiles_csv(FIXTURES / "route_profiles.csv"))
    vessel_types = load_vessel_type_map_csv(FIXTURES / "vessel_type_map.csv")
    appearance_map = load_appearance_map_csv(FIXTURES / "nstr_appearance_map.csv")

    result = trips_to_arc_requests(
        trips,
        routes=routes,
        vessel_types=vessel_types,
        appearance_map=appearance_map,
    )

    requests = arc_requests_to_frame(result.requests)
    rejections = trip_rejections_to_frame(result.rejections)

    print(requests.to_string(index=False))
    print()
    print(rejections.to_string(index=False))
    print()
    print(json.dumps(summarise_trip_conversion(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
