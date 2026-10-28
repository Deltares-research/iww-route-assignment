"""Traffic assignment: route trips over a network and price them with the cost function."""

from .network import MIN_SEARCH_WEIGHT, AssignmentNetwork, NetworkEdge
from .penalties import PenaltyDetail, PenaltyProfile, is_flat_beam_structure, penalty_profile
from .pricing import (
    CostCoverageReport,
    CostInputs,
    EdgeTravel,
    FuelRates,
    RoutePrice,
    check_cost_coverage,
    price_route,
    routing_loading_ratio,
    search_weights,
)
from .runner import AssignmentResult, assign_traffic, edge_table, edge_travel
from .settings import (
    DEFAULT_SEA_WATER_NAMES,
    AssignmentSettings,
    NavigabilityPenalties,
    RoutePreferences,
)
from .snapping import SNAP_CRS, EdgeSnapIndex, SnapResult, build_snap_index, snap_points
from .trips import AssignmentTrips, PreparedTrips, VesselCatalogue, prepare_trips

__all__ = [
    "DEFAULT_SEA_WATER_NAMES",
    "MIN_SEARCH_WEIGHT",
    "SNAP_CRS",
    "AssignmentNetwork",
    "AssignmentResult",
    "AssignmentSettings",
    "AssignmentTrips",
    "CostCoverageReport",
    "CostInputs",
    "EdgeSnapIndex",
    "EdgeTravel",
    "FuelRates",
    "NavigabilityPenalties",
    "NetworkEdge",
    "PenaltyDetail",
    "PenaltyProfile",
    "PreparedTrips",
    "RoutePreferences",
    "RoutePrice",
    "SnapResult",
    "VesselCatalogue",
    "assign_traffic",
    "build_snap_index",
    "check_cost_coverage",
    "edge_table",
    "edge_travel",
    "is_flat_beam_structure",
    "penalty_profile",
    "prepare_trips",
    "price_route",
    "routing_loading_ratio",
    "search_weights",
    "snap_points",
]
