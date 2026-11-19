"""Port for a route simulation that refines the routes of an assignment.

:func:`~iww_route_assignment.assignment.assign_traffic` finds routes with free-flow weights.
A simulator may then replay the trips in time and return the routes they actually sailed,
with the time they spent waiting. The assignment itself imports no simulation package, so
a simulator can be built on OpenTNSim or on anything else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from .router import RoutingContext, RoutingOutput


@dataclass(frozen=True, slots=True, eq=False)
class SimulationOutcome:
    """Routes after simulation, extra result tables and a summary of the simulation."""

    output: RoutingOutput
    tables: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)


class RouteSimulator(Protocol):
    """Replays trips and returns their routes with simulated times.

    ``trips`` is the table of routable trips, with ``departure_time`` (NaT when unknown).
    ``static`` holds the routes found without simulation. A simulator must return a trip
    row for every trip in ``static.trips``, and keep the trips it cannot simulate as they are.
    """

    def simulate(
        self, context: RoutingContext, trips: pd.DataFrame, static: RoutingOutput
    ) -> SimulationOutcome: ...
