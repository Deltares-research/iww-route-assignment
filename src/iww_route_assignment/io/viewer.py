"""Write an assignment result as one interactive HTML map.

The file holds the data as JSON and draws it with Leaflet in the browser. No Python map
library is needed. Viewing needs an internet connection for Leaflet and the background tiles.

On the map: one line per used section, width by trips, colour by the measure chosen in the
panel. Layers for penalised sections, locks and the unused network can be switched on and off.
Clicking a section shows its figures and every unmet limit. For a small run a trip can be
picked to see its route, beside the route of a second result when one is given.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely import LineString

from ..assignment import AssignmentNetwork, AssignmentResult
from ..domain.enums import TripStatus

MEASURES = (
    ("trips", "Trips", ""),
    ("cargo_t", "Cargo", "t"),
    ("cost_eur", "Reported cost", "EUR"),
    ("fuel_l", "Fuel", "l"),
    ("time_h", "Reported time", "h"),
    ("empty_share", "Share of empty trips", "%"),
    ("penalty_h", "Penalty hours", "h"),
)
"""Key, label and unit of each measure the map can colour by."""

TEMPLATE_FILE = Path(__file__).with_name("assignment_map_template.html")
"""The page: layout, styles and the Leaflet script. ``__DATA__`` is replaced by the JSON."""

PENALTY_KEYS = ("beam", "length", "draught", "air_draught", "undetermined", "cemt", "sea")


def _line_coordinates(
    network: AssignmentNetwork, index: int, tolerance_deg: float
) -> list[list[float]]:
    line = network.edge_lines[index]
    if line is None or line.is_empty:
        line = LineString(
            [
                network.node_lon_lat[network.edge_from[index]],
                network.node_lon_lat[network.edge_to[index]],
            ]
        )
    if tolerance_deg > 0:
        line = line.simplify(tolerance_deg, preserve_topology=False)
    return [[round(lat, 5), round(lon, 5)] for lon, lat, *_ in line.coords]


def _number(value: Any, digits: int = 2) -> float:
    number = float(value)
    return round(number, digits) if np.isfinite(number) else 0.0


def _edge_records(
    result: AssignmentResult, network: AssignmentNetwork, tolerance_deg: float, include_unused: bool
) -> list[dict[str, Any]]:
    used = result.intensity.set_index("edge_index")
    penalised: dict[int, list[dict[str, Any]]] = {}
    for row in result.penalised.itertuples(index=False):
        penalised.setdefault(int(row.edge_index), []).append(
            {
                "type": row.penalty_type,
                "structure": row.structure_id,
                "structure_type": row.structure_type,
                "limit_m": None if pd.isna(row.limit_m) else _number(row.limit_m),
                "penalty_h": _number(row.penalty_h),
                "trips": int(row.trips),
                "classes": row.vessel_classes,
            }
        )
    records = []
    for index, edge in enumerate(network.edges):
        is_used = index in used.index
        if not is_used and not include_unused:
            continue
        record: dict[str, Any] = {
            "i": index,
            "g": _line_coordinates(network, index, tolerance_deg),
            "u": int(is_used),
        }
        if is_used:
            row = used.loc[index]
            trips = int(row["trip_count"])
            record |= {
                "name": edge.name or edge.section_code or edge.arc_id,
                "code": edge.section_code,
                "cemt": edge.waterway_cemt_class or "",
                "km": _number(edge.distance_km, 3),
                "locks": edge.lock_count,
                "v": {
                    "trips": trips,
                    "cargo_t": _number(row["cargo_t"], 0),
                    "cost_eur": _number(row["reported_cost_eur"], 0),
                    "fuel_l": _number(row["fuel_l"], 0),
                    "time_h": _number(row["reported_time_h"], 1),
                    "empty_share": _number(100 * row["trip_count_empty"] / trips, 1),
                    "penalty_h": _number(row["penalty_total_h"], 0),
                },
                "p": penalised.get(index, []),
            }
        records.append(record)
    return records


def _trip_records(
    result: AssignmentResult,
    other: AssignmentResult | None,
    max_trips: int,
    trip_labels: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if len(result.trips) > max_trips:
        return [], ""
    routes = result.trip_edges.sort_values(["trip_id", "seq"]).groupby("trip_id")["edge_index"]
    route = {str(trip): [int(i) for i in indices] for trip, indices in routes}
    other_route: dict[str, list[int]] = {}
    other_label = ""
    other_trips = None
    if other is not None:
        grouped = other.trip_edges.sort_values(["trip_id", "seq"]).groupby("trip_id")["edge_index"]
        other_route = {str(trip): [int(i) for i in indices] for trip, indices in grouped}
        other_trips = other.trips.set_index("trip_id")
        other_label = str(other.trips["route_metric"].iloc[0])
    labels = dict(trip_labels or {})
    records = []
    for trip in result.trips.itertuples(index=False):
        routed = trip.status == TripStatus.SUCCESS.value
        short_id = str(trip.trip_id).rsplit(":", 1)[-1]
        record: dict[str, Any] = {
            "id": short_id,
            "label": labels.get(str(trip.trip_id), labels.get(short_id, "")),
            "status": trip.status,
            "detail": trip.detail,
            "cls": trip.vessel_rws_class if isinstance(trip.vessel_rws_class, str) else "",
            "app": trip.appearance,
            "cargo_t": _number(trip.cargo_t, 0),
            "e": route.get(str(trip.trip_id), []),
        }
        if routed:
            record |= {
                "km": _number(trip.distance_km, 1),
                "h": _number(trip.reported_time_h, 2),
                "fuel_l": _number(trip.fuel_l, 0),
                "distance_cost": _number(trip.distance_cost_eur, 0),
                "time_cost": _number(trip.time_cost_eur, 0),
                "fuel_cost": _number(trip.fuel_cost_eur, 0),
                "fixed_cost": _number(trip.fixed_cost_eur, 0),
                "cost": _number(trip.reported_total_cost_eur, 0),
                "penalty_h": _number(trip.penalty_total_h, 0),
                "confidence": trip.fuel_lowest_confidence,
            }
        if other_trips is not None and str(trip.trip_id) in other_route:
            alt = other_trips.loc[trip.trip_id]
            record["alt"] = {
                "e": other_route[str(trip.trip_id)],
                "km": _number(alt["distance_km"], 1),
                "h": _number(alt["reported_time_h"], 2),
                "cost": _number(alt["reported_total_cost_eur"], 0),
            }
        records.append(record)
    if labels:
        records.sort(key=lambda record: (record["label"] == "", record["label"], record["id"]))
    return records, other_label


def _summary(result: AssignmentResult) -> dict[str, Any]:
    trips = result.trips
    routed = trips[trips["status"] == TripStatus.SUCCESS.value]
    confidence = (
        routed["fuel_lowest_confidence"].isin(["low", "fallback"]).mean() if len(routed) else 0
    )
    return {
        "trips": int(len(trips)),
        "routed": int(len(routed)),
        "status": {str(k): int(v) for k, v in trips["status"].value_counts().sort_index().items()},
        "rejected": int(len(result.rejections)),
        "cost_eur": _number(routed["reported_total_cost_eur"].sum(), 0),
        "fuel_l": _number(routed["fuel_l"].sum(), 0),
        "km": _number(routed["distance_km"].sum(), 0),
        "penalised_trips": int((routed["penalised_section_count"] > 0).sum()),
        "fuel_proxy_share": _number(100 * confidence, 1),
        "metric": str(result.summary["route_metric"]),
    }


def assignment_map_data(
    result: AssignmentResult,
    network: AssignmentNetwork,
    *,
    title: str = "Traffic assignment",
    other: AssignmentResult | None = None,
    max_trips: int = 300,
    simplify_tolerance_deg: float = 0.0003,
    include_unused: bool = True,
    trip_labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the JSON-ready data behind the map. Useful for tests and other viewers."""
    trips, other_label = _trip_records(result, other, max_trips, trip_labels)
    return {
        "title": title,
        "summary": _summary(result),
        "measures": [{"key": k, "label": label, "unit": unit} for k, label, unit in MEASURES],
        "penalty_types": list(PENALTY_KEYS),
        "edges": _edge_records(result, network, simplify_tolerance_deg, include_unused),
        "trips": trips,
        "other_label": other_label,
    }


def write_assignment_map(
    result: AssignmentResult,
    network: AssignmentNetwork,
    path: str | Path,
    *,
    title: str = "Traffic assignment",
    other: AssignmentResult | None = None,
    max_trips: int = 300,
    simplify_tolerance_deg: float = 0.0003,
    include_unused: bool = True,
    trip_labels: Mapping[str, str] | None = None,
) -> Path:
    """Write the interactive map to ``path`` and return it.

    ``other`` is a second result for the same trips, such as the time-based routes beside the
    cost-based ones. Trips can be picked one by one when the run has at most ``max_trips``.
    ``simplify_tolerance_deg`` thins the lines to keep the file small. 0 keeps every vertex.
    ``trip_labels`` maps a trip id, full or without its dataset prefix, to a short name shown
    in the trip picker. Labelled trips are listed first, in label order.
    """
    data = assignment_map_data(
        result,
        network,
        title=title,
        other=other,
        max_trips=max_trips,
        simplify_tolerance_deg=simplify_tolerance_deg,
        include_unused=include_unused,
        trip_labels=trip_labels,
    )
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    html = template.replace("__TITLE__", title.replace("<", "&lt;")).replace("__DATA__", payload)
    target.write_text(html, encoding="utf-8")
    return target
