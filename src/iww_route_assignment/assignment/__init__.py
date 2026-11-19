"""Traffic assignment: route trips over a network and price them with the cost function."""

from .lock_placement import SIDE_A, SIDE_B, LockPlacement, LockPlacements, entry_side, place_locks
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
from .router import RoutingCaches, RoutingContext, RoutingOutput, record_route
from .runner import AssignmentResult, assign_traffic, edge_table, edge_travel
from .settings import (
    DEFAULT_SEA_WATER_NAMES,
    AssignmentSettings,
    NavigabilityPenalties,
    RoutePreferences,
)
from .simulation_port import RouteSimulator, SimulationOutcome
from .snapping import SNAP_CRS, EdgeSnapIndex, SnapResult, build_snap_index, snap_points
from .trips import AssignmentTrips, PreparedTrips, VesselCatalogue, prepare_trips

__all__ = [
    "SIDE_A",
    "SIDE_B",
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
    "LockPlacement",
    "LockPlacements",
    "NavigabilityPenalties",
    "NetworkEdge",
    "PenaltyDetail",
    "PenaltyProfile",
    "PreparedTrips",
    "RoutePreferences",
    "RoutePrice",
    "RouteSimulator",
    "RoutingCaches",
    "RoutingContext",
    "RoutingOutput",
    "SimulationOutcome",
    "SnapResult",
    "VesselCatalogue",
    "assign_traffic",
    "build_snap_index",
    "check_cost_coverage",
    "edge_table",
    "entry_side",
    "edge_travel",
    "is_flat_beam_structure",
    "penalty_profile",
    "place_locks",
    "prepare_trips",
    "price_route",
    "record_route",
    "routing_loading_ratio",
    "search_weights",
    "snap_points",
]
