"""Check supplied synthetic routes against hard navigability restrictions.

Navigability is evaluated independently of costs. The example writes nothing and uses only the
synthetic repository fixtures.
"""

from pathlib import Path

from iww_route_assignment import (
    NavigabilityMargins,
    build_restriction_index,
    evaluate_route,
    navigable_arc_mask,
    restrict_choice_weights,
)
from iww_route_assignment.io import (
    load_arc_profiles_csv,
    load_arc_restrictions_csv,
    load_navigability_vessels_csv,
    load_route_sequences_csv,
    route_navigability_to_frames,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> None:
    arcs = load_arc_profiles_csv(FIXTURES / "arc_profiles.csv")
    routes = load_route_sequences_csv(FIXTURES / "route_sequences.csv", arcs)
    index = build_restriction_index(load_arc_restrictions_csv(FIXTURES / "arc_restrictions.csv"))
    vessels = load_navigability_vessels_csv(FIXTURES / "navigability_vessels.csv")
    margins = NavigabilityMargins(under_keel_clearance_m=0.0, air_clearance_m=0.0)

    results = [
        evaluate_route(route, vessel, index, margins) for route in routes for vessel in vessels
    ]
    summary, findings = route_navigability_to_frames(results)
    print(summary.to_string(index=False))
    print()
    print(findings.to_string(index=False))

    arc_ids = [arc.arc_id for arc in arcs]
    mask = navigable_arc_mask(arc_ids, vessels[0], index, margins)
    weights = restrict_choice_weights([100.0] * len(arc_ids), mask).tolist()
    print()
    print(f"Choice weights for {vessels[0].vessel_id}: {dict(zip(arc_ids, weights, strict=True))}")


if __name__ == "__main__":
    main()
