"""Turn validated trips into voyages ready for routing: vessel, cost class, snapped points."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd

from ..domain.classifications import normalise_rws_class
from ..domain.enums import MissingCostPolicy, RouteCostMetric, VoyageStatus
from ..domain.models import TripObservation, TripRejection, VesselReference
from ..domain.navigability import NavigabilityVessel
from ..exceptions import ValidationError
from ..preparation import DEFAULT_NSTR_APPEARANCE, appearance_for_trip, loading_ratio_for_trip
from .network import AssignmentNetwork
from .pricing import CostCoverageReport, CostInputs, check_cost_coverage, routing_loading_ratio
from .settings import AssignmentSettings
from .snapping import EdgeSnapIndex, snap_points

VOYAGE_PENDING = "pending"


@dataclass(frozen=True, slots=True, eq=False)
class AssignmentTrips:
    """Validated trips with their origin and destination points, in the same order."""

    trips: tuple[TripObservation, ...]
    origins: gpd.GeoSeries
    destinations: gpd.GeoSeries
    rejections: tuple[TripRejection, ...] = ()
    excluded_duplicate_rows: int = 0

    def __post_init__(self) -> None:
        if not (len(self.trips) == len(self.origins) == len(self.destinations)):
            raise ValidationError("trips, origins and destinations must align")
        if self.origins.crs is None or self.destinations.crs is None:
            raise ValidationError("origins and destinations need a CRS")


@dataclass(frozen=True, slots=True, eq=False)
class VesselCatalogue:
    """Vessel dimensions per RWS class, and optional navigability states.

    ``navigability_vessels`` uses identifiers ``<class>-loaded`` and ``<class>-empty``. A
    class without such a state uses its reference dimensions, without air draught.
    ``class_aliases`` maps a source label to a reference class. It is empty by default:
    labels such as B04 and BO4 are never treated as the same class unless stated here.
    ``io.IVS_SK_CODE_CROSSWALK`` holds the confirmed IVS mapping.
    """

    references: tuple[VesselReference, ...]
    navigability_vessels: tuple[NavigabilityVessel, ...] = ()
    class_aliases: Mapping[str, str] = field(default_factory=dict)
    by_class: Mapping[str, VesselReference] = field(init=False, repr=False)
    states: Mapping[tuple[str, bool], NavigabilityVessel] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        by_class: dict[str, VesselReference] = {}
        for reference in self.references:
            if reference.vessel_rws_class in by_class:
                raise ValidationError(f"Duplicate vessel reference {reference.vessel_rws_class!r}")
            by_class[reference.vessel_rws_class] = reference
        states: dict[tuple[str, bool], NavigabilityVessel] = {}
        for vessel in self.navigability_vessels:
            label, _, state = vessel.vessel_id.rpartition("-")
            if state not in {"loaded", "empty"} or not label:
                continue
            key = (normalise_rws_class(label), state == "loaded")
            if key in states:
                raise ValidationError(f"Duplicate navigability vessel {vessel.vessel_id!r}")
            states[key] = vessel
        aliases = {
            normalise_rws_class(source): normalise_rws_class(target)
            for source, target in self.class_aliases.items()
        }
        # Plain dicts, not MappingProxyType, so the catalogue can be sent to worker processes.
        object.__setattr__(self, "by_class", by_class)
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "class_aliases", aliases)

    def resolve_class(self, label: str) -> str | None:
        """Return the reference class for a source label, or None when unknown."""
        if not label.strip():
            return None
        canonical = normalise_rws_class(label)
        canonical = self.class_aliases.get(canonical, canonical)
        return canonical if canonical in self.by_class else None

    def navigability_vessel(self, vessel_rws_class: str, is_loaded: bool) -> NavigabilityVessel:
        """Dimensions used for limits. A loaded vessel uses its full loaded draught."""
        state = self.states.get((vessel_rws_class, is_loaded))
        if state is not None:
            return state
        return NavigabilityVessel.from_reference(
            self.by_class[vessel_rws_class], is_loaded=is_loaded
        )


@dataclass(frozen=True, slots=True, eq=False)
class PreparedVoyages:
    """One row per trip, with status ``pending`` for voyages that can be routed."""

    table: pd.DataFrame
    coverage: CostCoverageReport


def _cost_class(
    trip: TripObservation,
) -> tuple[str, str, float, str, str]:
    """Return appearance, its source, loading ratio, its source and an error text."""
    try:
        appearance, appearance_source = appearance_for_trip(trip, DEFAULT_NSTR_APPEARANCE)
    except ValidationError as error:
        return "", "", np.nan, "", f"no cargo appearance: {error}"
    if trip.cargo_t is None:
        return appearance, appearance_source, np.nan, "", "cargo weight missing for a loaded trip"
    if trip.cargo_t == 0:
        return appearance, appearance_source, 0.0, "empty-trip", ""
    try:
        ratio, ratio_source, _ = loading_ratio_for_trip(trip)
    except ValidationError as error:
        return appearance, appearance_source, np.nan, "", f"no loading ratio: {error}"
    return appearance, appearance_source, ratio, ratio_source, ""


def _set_status(
    table: pd.DataFrame, rows: pd.Series, status: VoyageStatus, detail: str | pd.Series
) -> None:
    """Set a failure status on rows that are still pending; earlier failures win."""
    mask = (rows & (table["status"] == VOYAGE_PENDING)).to_numpy(dtype=bool)
    table.loc[mask, "status"] = status.value
    values = detail if isinstance(detail, str) else detail.to_numpy(dtype=object)[mask]
    table.loc[mask, "detail"] = values


def prepare_voyages(
    trips: AssignmentTrips,
    network: AssignmentNetwork,
    snap_index: EdgeSnapIndex,
    catalogue: VesselCatalogue,
    cost_inputs: CostInputs,
    settings: AssignmentSettings,
) -> PreparedVoyages:
    """Snap points, resolve vessel and cost class, and set a status per voyage.

    Checks run in this order and the first failure sets the status: snapping, vessel
    class, then cost inputs. Voyages that pass every check are ``pending``.
    """
    records = []
    for index, trip in enumerate(trips.trips):
        appearance, appearance_source, ratio, ratio_source, cost_error = _cost_class(trip)
        records.append(
            {
                "voyage_index": index,
                "trip_id": trip.source_row_id,
                "source_vessel_class": trip.vessel_rws_class,
                "vessel_rws_class": catalogue.resolve_class(trip.vessel_rws_class),
                "appearance": appearance,
                "appearance_source": appearance_source,
                "is_loaded": bool(trip.cargo_t is None or trip.cargo_t > 0),
                "cargo_t": np.nan if trip.cargo_t is None else trip.cargo_t,
                "capacity_t": trip.capacity_t,
                "loading_ratio": ratio,
                "loading_ratio_source": ratio_source,
                "cost_error": cost_error,
            }
        )
    table = pd.DataFrame.from_records(records)
    table["status"] = VOYAGE_PENDING
    table["detail"] = ""

    for prefix, points in (("origin", trips.origins), ("destination", trips.destinations)):
        snap = snap_points(points.reset_index(drop=True), snap_index, network)
        table[f"{prefix}_node"] = snap.node
        table[f"{prefix}_node_id"] = [network.node_ids[n] if n >= 0 else "" for n in snap.node]
        table[f"{prefix}_snap_m"] = snap.edge_distance_m
        table[f"{prefix}_node_distance_m"] = snap.node_distance_m
    no_point = (table["origin_node"] < 0) | (table["destination_node"] < 0)
    _set_status(table, no_point, VoyageStatus.SNAP_FAIL, "missing origin or destination point")
    if settings.max_snap_distance_m is not None:
        too_far = (table["origin_snap_m"] > settings.max_snap_distance_m) | (
            table["destination_snap_m"] > settings.max_snap_distance_m
        )
        detail = f"point further than {settings.max_snap_distance_m} m from the network"
        _set_status(table, too_far, VoyageStatus.SNAP_FAIL, detail)

    unknown = table["vessel_rws_class"].isna()
    unknown_detail = "no vessel reference for class " + table["source_vessel_class"].astype(str)
    _set_status(table, unknown, VoyageStatus.VESSEL_FAIL, unknown_detail)

    known = table["vessel_rws_class"].notna() & (table["appearance"] != "")
    coverage = check_cost_coverage(
        zip(table.loc[known, "vessel_rws_class"], table.loc[known, "appearance"], strict=True),
        cost_inputs,
        catalogue.by_class,
    )
    unknown_sources = table.loc[
        unknown & (table["source_vessel_class"] != ""), "source_vessel_class"
    ]
    coverage = CostCoverageReport(
        tuple(sorted(set(unknown_sources.astype(str)))), coverage.missing_rates
    )
    if coverage.missing_rates and settings.on_missing_cost is MissingCostPolicy.FAIL:
        raise ValidationError(f"Cost rates missing for {list(coverage.missing_rates)}")
    missing = set(coverage.missing_rates)
    no_rates = pd.Series(
        [
            (v, a) in missing
            for v, a in zip(table["vessel_rws_class"], table["appearance"], strict=True)
        ],
        index=table.index,
    )
    rate_detail = (
        "no cost rates for " + table["vessel_rws_class"].astype(str) + "/" + table["appearance"]
    )
    _set_status(table, table["cost_error"] != "", VoyageStatus.COST_FAIL, table["cost_error"])
    _set_status(table, no_rates, VoyageStatus.COST_FAIL, rate_detail)

    table["routing_loading_ratio"] = [
        routing_loading_ratio(ratio, settings.routing_ratio_step) if ratio == ratio else np.nan
        for ratio in table["loading_ratio"]
    ]
    key_columns = ["vessel_rws_class", "is_loaded"]
    if settings.metric is RouteCostMetric.TRAVEL_COST_EUR:
        key_columns += ["appearance", "routing_loading_ratio"]
    pending = table["status"] == VOYAGE_PENDING
    keys = table.loc[pending, key_columns].astype(str).agg("|".join, axis=1)
    table["routing_key"] = ""
    table.loc[pending, "routing_key"] = keys
    return PreparedVoyages(table.drop(columns=["cost_error"]), coverage)
