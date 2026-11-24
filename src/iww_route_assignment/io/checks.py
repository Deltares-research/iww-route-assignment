"""One check table per module, so each step of a run can be judged on its own.

``module_check_tables`` returns five tables for a finished run:

1. ``trips``: each source row beside the trip that was read from it.
2. ``network``: each used section with its EuRIS attributes and the limits built from them.
3. ``dimension_control``: each unmet limit, with the ship value, the limit and the penalty.
4. ``cost``: each sailed section with its cost parts beside a recomputation from the raw
   cost-rate row, made here without the cost module.
5. ``assignment``: each trip with its route and totals, beside a second run when given.

Use it on a small case. On a full register the tables are large.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from ..assignment import AssignmentResult
from ..domain.enums import TripStatus
from ..navigability import build_restriction_index, evaluate_arc
from .workflow import AssignmentRun

HAND_CHECK_TOLERANCE_EUR = 1e-9

TRIP_SOURCE_COLUMNS = (
    "Pseudo_ReisID",
    "SK_CODE",
    "Segment",
    "v28_Beladingscode",
    "Lading",
    "Laadvermogen",
    "Eerste_Tijdstip",
)


def _keyed_rates(cost_rates: pd.DataFrame) -> pd.DataFrame:
    """Index the raw cost-rate table by (vessel class in lower case, appearance)."""
    keys = pd.MultiIndex.from_arrays(
        [cost_rates["rws_class"].str.casefold(), cost_rates["appearance"]]
    )
    rates = cost_rates.set_axis(keys, axis=0)
    return rates[~rates.index.duplicated()]


def _pseudo_id(trip_id: str) -> str:
    return str(trip_id).rsplit(":", 1)[-1]


def trips_check(run: AssignmentRun) -> pd.DataFrame:
    """Source columns beside what the reader made of them. Rejected rows are listed too."""
    read = run.result.trips[
        [
            "trip_id",
            "status",
            "detail",
            "source_vessel_class",
            "vessel_rws_class",
            "appearance",
            "appearance_source",
            "is_loaded",
            "cargo_t",
            "capacity_t",
            "loading_ratio",
            "departure_time",
            "origin_node_id",
            "origin_snap_m",
            "destination_node_id",
            "destination_snap_m",
        ]
    ].copy()
    read["Pseudo_ReisID"] = read["trip_id"].map(_pseudo_id)
    rejected = pd.DataFrame(
        {
            "trip_id": [r.source_row_id for r in run.result.rejections],
            "status": "rejected",
            "detail": [r.detail for r in run.result.rejections],
        }
    )
    rejected["Pseudo_ReisID"] = rejected["trip_id"].map(_pseudo_id)
    table = pd.concat([read, rejected], ignore_index=True)
    columns = [c for c in TRIP_SOURCE_COLUMNS if c in run.inputs.source_frame.columns]
    source = run.inputs.source_frame[columns].drop_duplicates("Pseudo_ReisID")
    source = source.rename(columns={c: f"source_{c}" for c in columns if c != "Pseudo_ReisID"})
    merged = source.merge(table, on="Pseudo_ReisID", how="outer").sort_values(
        "Pseudo_ReisID", ignore_index=True
    )
    # A source row that gave neither a trip nor a rejection was dropped for a repeated id.
    dropped = merged["status"].isna()
    merged.loc[dropped, "status"] = "excluded"
    merged.loc[dropped, "detail"] = "repeated trip id; every row with this id is left out"
    return merged


def network_check(run: AssignmentRun) -> pd.DataFrame:
    """Each used section: length, speed, class, locks, and every limit with its source."""
    used = run.result.intensity
    edges = pd.DataFrame(
        {
            "edge_index": used["edge_index"],
            "arc_id": used["arc_id"],
            "section_code": used["section_code"],
            "name": used["name"],
            "cemt_class": used["cemt_class"],
            "length_km": used["length_km"],
            "speed_km_per_h": run.inputs.network.array("speed_km_per_h")[used["edge_index"]],
            "lock_count": used["lock_count"],
            "lock_time_h": run.inputs.network.array("lock_service_time_h")[used["edge_index"]],
            "trip_count": used["trip_count"],
        }
    )
    limits = pd.DataFrame(
        [
            {
                "arc_id": r.arc_id,
                "structure_type": r.structure_type.value,
                "structure_id": r.structure_id,
                "max_beam_m": r.max_beam_m,
                "max_length_m": r.max_length_m,
                "max_draught_m": r.max_draught_m,
                "max_air_draught_m": r.max_air_draught_m,
                "limit_source": r.source,
            }
            for r in run.inputs.restrictions
        ]
    )
    if limits.empty:
        return edges
    return edges.merge(limits, on="arc_id", how="left").sort_values(
        ["arc_id", "structure_type", "structure_id"], ignore_index=True
    )


def dimension_control_check(run: AssignmentRun) -> pd.DataFrame:
    """Each unmet or undetermined limit on a sailed section, per trip, with its penalty."""
    index = build_restriction_index(run.inputs.restrictions)
    margins = run.config.penalties.margins
    trips = run.result.trips.set_index("trip_id")
    rows = []
    for trip_id, edges in run.result.trip_edges.groupby("trip_id", sort=True):
        trip = trips.loc[trip_id]
        vessel = run.inputs.catalogue.navigability_vessel(
            str(trip["vessel_rws_class"]), bool(trip["is_loaded"])
        )
        edge: Any
        for edge in edges.sort_values("seq").itertuples(index=False):
            arc = evaluate_arc(str(edge.arc_id), vessel, index, margins, require_coverage=False)
            for finding in arc.findings:
                kind = finding.restriction.value
                rows.append(
                    {
                        "trip_id": trip_id,
                        "seq": edge.seq,
                        "arc_id": edge.arc_id,
                        "vessel_rws_class": trip["vessel_rws_class"],
                        "is_loaded": bool(trip["is_loaded"]),
                        "restriction": kind,
                        "status": finding.status.value,
                        "structure_type": finding.structure_type.value
                        if finding.structure_type
                        else "",
                        "structure_id": finding.structure_id or "",
                        "ship_value_plus_margin_m": finding.required_m,
                        "limit_m": finding.limit_m,
                        "penalty_h_for_this_type_on_section": getattr(
                            edge, f"penalty_{kind}_h", np.nan
                        ),
                        "limit_source": finding.source,
                    }
                )
    columns = [
        "trip_id",
        "seq",
        "arc_id",
        "vessel_rws_class",
        "is_loaded",
        "restriction",
        "status",
        "structure_type",
        "structure_id",
        "ship_value_plus_margin_m",
        "limit_m",
        "penalty_h_for_this_type_on_section",
        "limit_source",
    ]
    return pd.DataFrame(rows, columns=columns)


def cost_check(run: AssignmentRun, cost_rates: pd.DataFrame) -> pd.DataFrame:
    """Each sailed section with a recomputation of its cost from the raw cost-rate row.

    ``cost_rates`` is the raw ``cost_rates.csv`` table. The recomputation uses only that row,
    the scenario and the columns of the section, not the cost module:

    hours = km / speed + lock hours. hourly rate = labor_h + maint_h + insurance_h + interest
    percent x interest_pct_h + depreciation_h + harbor_h + other_h. fuel litres = litres per km
    x km + idle factor x litres per km x speed x lock hours. cost = maint_km x km + hourly rate
    x hours + fuel litres x fuel price.
    """
    scenario = run.config.scenario
    trips = run.result.trips.set_index("trip_id")
    edges = run.result.trip_edges.copy()
    key = edges["trip_id"].map(
        lambda t: (str(trips.loc[t, "vessel_rws_class"]).casefold(), trips.loc[t, "appearance"])
    )
    rates = _keyed_rates(cost_rates)
    row = rates.loc[list(key)].reset_index(drop=True)
    hourly = (
        row["labor_h"]
        + row["maint_h"]
        + row["insurance_h"]
        + scenario.interest_rate_percent * row["interest_pct_h"]
        + row["depreciation_h"]
        + row["harbor_h"]
        + row["other_h"]
    ).to_numpy()
    km = edges["distance_km"].to_numpy()
    speed = edges["speed_km_per_h"].to_numpy()
    lock_h = edges["lock_time_h"].to_numpy()
    rate = edges["fuel_rate_l_per_km"].to_numpy()
    hours = km / speed + lock_h
    fuel_l = rate * km + scenario.idle_fuel_factor * rate * speed * lock_h
    edges["cost_rate_row"] = (row["appearance"] + "/" + row["rws_class"]).to_numpy()
    edges["hourly_rate_eur_per_h"] = hourly
    edges["hand_time_h"] = hours
    edges["hand_fuel_l"] = fuel_l
    edges["hand_distance_cost_eur"] = row["maint_km"].to_numpy() * km
    edges["hand_time_cost_eur"] = hourly * hours
    edges["hand_fuel_cost_eur"] = fuel_l * scenario.fuel_price_eur_per_l
    edges["hand_reported_cost_eur"] = (
        edges["hand_distance_cost_eur"] + edges["hand_time_cost_eur"] + edges["hand_fuel_cost_eur"]
    )
    edges["difference_eur"] = edges["reported_cost_eur"] - edges["hand_reported_cost_eur"]
    columns = [
        "trip_id",
        "seq",
        "arc_id",
        "distance_km",
        "speed_km_per_h",
        "lock_count",
        "lock_time_h",
        "fuel_rate_l_per_km",
        "fuel_method",
        "fuel_confidence",
        "cost_rate_row",
        "hourly_rate_eur_per_h",
        "reported_time_h",
        "hand_time_h",
        "fuel_l",
        "hand_fuel_l",
        "distance_cost_eur",
        "hand_distance_cost_eur",
        "time_cost_eur",
        "hand_time_cost_eur",
        "fuel_cost_eur",
        "hand_fuel_cost_eur",
        "reported_cost_eur",
        "hand_reported_cost_eur",
        "difference_eur",
    ]
    return edges[columns].sort_values(["trip_id", "seq"], ignore_index=True)


def _route_text(result: AssignmentResult) -> pd.Series:
    edges = result.trip_edges.sort_values(["trip_id", "seq"])
    return edges.groupby("trip_id")["arc_id"].agg(lambda arcs: " > ".join(arcs))


def assignment_check(
    run: AssignmentRun, cost_rates: pd.DataFrame, other: AssignmentResult | None = None
) -> pd.DataFrame:
    """Each trip: status, totals, the hand total with the fixed cost, and its route.

    ``other`` is a second result for the same trips, usually the other route-search metric.
    Its distance, time, cost and route are shown beside the first.
    """
    scenario = run.config.scenario
    trips = run.result.trips.copy()
    cost = cost_check(run, cost_rates)
    arc_sum = cost.groupby("trip_id")["hand_reported_cost_eur"].sum()
    rates = _keyed_rates(cost_rates)

    def fixed(trip: pd.Series) -> float:
        if trip["status"] != TripStatus.SUCCESS.value:
            return np.nan
        if not trip["cargo_t"] > 0:
            return 0.0
        row = rates.loc[(str(trip["vessel_rws_class"]).casefold(), trip["appearance"])]
        return float(
            (row["load_t"] + row["unload_t"]) * trip["cargo_t"]
            + row["labor_wait_h"] * scenario.cargo_waiting_time_h
        )

    trips["hand_fixed_cost_eur"] = trips.apply(fixed, axis=1)
    trips["hand_reported_total_cost_eur"] = (
        trips["trip_id"].map(arc_sum).fillna(0.0) + trips["hand_fixed_cost_eur"]
    )
    trips["difference_eur"] = (
        trips["reported_total_cost_eur"] - trips["hand_reported_total_cost_eur"]
    )
    trips["route"] = trips["trip_id"].map(_route_text(run.result))
    columns = [
        "trip_id",
        "status",
        "detail",
        "route_metric",
        "vessel_rws_class",
        "appearance",
        "cargo_t",
        "loading_ratio",
        "routing_loading_ratio",
        "n_edges",
        "lock_passages",
        "distance_km",
        "reported_time_h",
        "fuel_l",
        "distance_cost_eur",
        "time_cost_eur",
        "fuel_cost_eur",
        "fixed_cost_eur",
        "hand_fixed_cost_eur",
        "reported_total_cost_eur",
        "hand_reported_total_cost_eur",
        "difference_eur",
        "penalty_total_h",
        "choice_total_cost_eur",
        "fuel_lowest_confidence",
        "route",
    ]
    table = trips[[c for c in columns if c in trips.columns]]
    if other is not None:
        second = other.trips.set_index("trip_id")
        label = str(second["route_metric"].iloc[0])
        for name in ("distance_km", "reported_time_h", "reported_total_cost_eur"):
            table[f"{name}__{label}"] = table["trip_id"].map(second[name])
        table[f"route__{label}"] = table["trip_id"].map(_route_text(other))
        table[f"same_route_as_{label}"] = table["route"].fillna("") == table[
            f"route__{label}"
        ].fillna("")
    return table.sort_values("trip_id", ignore_index=True)


def module_check_tables(
    run: AssignmentRun, cost_rates: pd.DataFrame, other: AssignmentResult | None = None
) -> Mapping[str, pd.DataFrame]:
    """Return the five check tables by name, in workflow order."""
    return {
        "1_trips": trips_check(run),
        "2_network": network_check(run),
        "3_dimension_control": dimension_control_check(run),
        "4_cost": cost_check(run, cost_rates),
        "5_assignment": assignment_check(run, cost_rates, other),
    }


def largest_hand_difference_eur(tables: Mapping[str, pd.DataFrame]) -> float:
    """Largest gap between library and hand recomputation, over sections and trips."""
    gaps = [tables[name]["difference_eur"].abs().max() for name in ("4_cost", "5_assignment")]
    present = [float(g) for g in gaps if pd.notna(g)]
    return max(present, default=0.0)
