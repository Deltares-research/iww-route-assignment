"""Discrete event simulation of lock queues on an assignment network (optional extra).

Install with ``pip install "iww-route-assignment[simulation]"``. The package needs OpenTNSim
and packer2d. The route assignment itself never imports it.
"""

from .graph import build_opentnsim_graph
from .inspect import inspect_trip
from .locks import Chamber, Lock, LockPassage, Locks
from .packing import select_vessels
from .rerouting import RerouteEvent, Rerouter
from .simulator import LockSimulationSettings, LockSimulator
from .vessel import RouteData, RouteVessel

__all__ = [
    "Chamber",
    "Lock",
    "LockPassage",
    "LockSimulationSettings",
    "LockSimulator",
    "Locks",
    "RerouteEvent",
    "Rerouter",
    "RouteData",
    "RouteVessel",
    "build_opentnsim_graph",
    "inspect_trip",
    "select_vessels",
]
