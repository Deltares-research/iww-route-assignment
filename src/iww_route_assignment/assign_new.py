"""Route assignment of individual IVS voyages on an inland-waterway graph.

Every input row is one voyage (``Pseudo_ReisID``). Its first and last counting
points (``Eerste_Geom`` and ``Laatste_Geom``) are snapped to the nearest graph edge,
and then to the nearer of that edge's two nodes. The voyage is routed between those
nodes. The route search minimises
travel time or travel cost in EUR (:class:`RouteCostMetric`), using the cost
calculators of this package. Costs, time and fuel are reported under both metrics.

Voyages that share the same routing inputs and origin node share one Dijkstra
search. This gives the same routes as searching per voyage, but is much faster.
"""

from __future__ import annotations

import logging
import pathlib
import pickle
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely import LineString, STRtree

from .costs.calculator import calculate_arc_choice_time_h, calculate_arc_costs
from .domain.classifications import (
    cemt_rank,
    get_ship_dimensions,
    is_valid,
    load_shiptypes,
    normalise_cemt_class,
    profile_for_appearance,
)
from .domain.enums import RouteCostMetric
from .domain.models import ArcTravelInput, ScenarioCostParameters, TripCostInput
from .exceptions import IWWRouteAssignmentError, ValidationError
from .io.loaders import load_cost_rates_csv, load_fuel_provider, select_cost_rates
from .routing_costs import RoutePricing, RoutingCostConfig, VoyageCostModel, _loading_ratio

logger = logging.getLogger(__name__)

# Graph edge attributes.
EDGE_LENGTH_M_ATTR = "length_m"
EDGE_CEMT_ATTR = "cemt"
EDGE_SPEED_ATTRS = ("maxspeed_up", "maxspeed_down", "maxspeed", "calspeed_up", "calspeed_down")
EDGE_WATER_NAME_ATTRS = ("water_name", "ww_name")

DEFAULT_SPEED_KM_PER_H = 10.0
MIN_SPEED_KM_PER_H = 1.0
DEFAULT_LOCK_PASSAGE_MIN = 30.0
# Soft-constraint penalties are expressed in hours of route-choice time.
PENALTY_UNIT_TO_HOURS = 1.0

PENALTY_KEYS = ("lock", "bridge", "fairway", "sea", "cemt")
DUTCH_WIDTH_PENALTY = 1000.0
FOREIGN_WIDTH_PENALTY_PER_M = 5.0
LOCK_LENGTH_PENALTY = 1000.0
SEA_PENALTY = 100000.0
CEMT_PENALTY = 1000.0
SEA_WATER_NAMES = frozenset({"noordzee", "north sea", "nordsee", "mer du nord"})

# IVS input format.
IVS_POINTS_CRS = "EPSG:28992"
SNAP_CRS = "EPSG:3035"
ORIGIN_GEOM_COL = "Eerste_Geom"
DESTINATION_GEOM_COL = "Laatste_Geom"
REQUIRED_IVS_COLUMNS = (
    "Pseudo_ReisID",
    "SK_CODE",
    "Segment",
    "Lading",
    "Laadvermogen",
    ORIGIN_GEOM_COL,
    DESTINATION_GEOM_COL,
)
OPTIONAL_IVS_COLUMNS = {
    "UNLO_Herkomst": "unlo_herkomst",
    "UNLO_Bestemming": "unlo_bestemming",
    "Eerste_Telpunt": "eerste_telpunt",
    "Laatste_Telpunt": "laatste_telpunt",
}

SEGMENT_APPEARANCE: Mapping[str, str] = {
    "Droge bulkvaart": "droge_bulk",
    "Tankvaart": "natte_bulk",
    "Containervaart": "container",
    "Overig": "overig",
}
SEGMENT_APPEARANCE_SOURCE = "assumption:ivs-segment-appearance-v1"

STATUS_PENDING = "pending"
STATUS_SUCCESS = "success"
STATUS_NO_PATH = "no_path"
STATUS_SNAP_FAIL = "snap_fail"
STATUS_VESSEL_FAIL = "vessel_fail"
STATUS_COST_FAIL = "cost_fail"


# --------------------------------------------------------------------------- graph


def load_graph(graph_path: pathlib.Path) -> nx.Graph:
    """Load a pickled networkx graph, e.g. ``fis-crawler/output/euris-graph/graph.pickle``."""
    logger.info(f"Loading graph from {graph_path}...")
    with open(graph_path, "rb") as f:
        return pickle.load(f)


def get_edge_key(d: Mapping[str, Any]) -> str | None:
    """Return the FIS section ID or EURIS section code of a graph edge."""
    val = d.get("fis_id") or d.get("code")
    return str(val) if is_valid(val) else None


def _number(val: Any) -> float | None:
    """Return a finite float from a number or a text such as '12 km/h'."""
    if not is_valid(val):
        return None
    if isinstance(val, str):
        match = re.search(r"[-+]?\d*\.\d+|\d+", val)
        if not match:
            return None
        val = match.group(0)
    try:
        number = float(val)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _first_number(record: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        number = _number(record.get(key))
        if number is not None:
            return number
    return None


def get_edge_speed_km_per_h(d: Mapping[str, Any]) -> float:
    """Return the first positive speed limit or calculation speed of an edge."""
    for key in EDGE_SPEED_ATTRS:
        speed = _number(d.get(key))
        if speed is not None and speed > 0.0:
            return max(MIN_SPEED_KM_PER_H, speed)
    return DEFAULT_SPEED_KM_PER_H


def _edge_cemt_class(d: Mapping[str, Any]) -> str | None:
    """Return the canonical CEMT class of an edge, or None when absent or unclassified."""
    val = d.get(EDGE_CEMT_ATTR)
    if not is_valid(val):
        return None
    try:
        cemt = normalise_cemt_class(str(val))
    except ValidationError:
        return None
    return cemt if cemt_rank(cemt) > 0 else None


def _fuel_cemt_class(d: Mapping[str, Any], ship_cemt: str) -> str:
    """Waterway class for the fuel lookup; the ship's own class when the edge has none."""
    return _edge_cemt_class(d) or ship_cemt


# ---------------------------------------------------------------- structures


def _lock_structure(lock_id: str, chambers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise a lock: a ship fits when it fits its widest and longest chamber."""
    widths = [w for ch in chambers if (w := _first_number(ch, ("dim_gate_width", "width")))]
    lengths = [n for ch in chambers if (n := _first_number(ch, ("dim_usable_length", "length")))]
    passages = [p for ch in chambers if (p := _number(ch.get("passage_duration_m")))]
    passage_min = float(np.mean(passages)) if passages else DEFAULT_LOCK_PASSAGE_MIN
    name = next(
        (str(ch[k]) for ch in chambers for k in ("lock_name", "name") if is_valid(ch.get(k))),
        "Lock",
    )
    return {
        "type": "lock",
        "id": lock_id,
        "name": name,
        "width_m": max(widths) if widths else None,
        "length_m": max(lengths) if lengths else None,
        "passage_h": passage_min / 60.0,
    }


def _bridge_structure(bridge_id: str, openings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise a bridge: a ship fits when it fits its widest opening."""
    width_keys = ("dim_structural_width", "dim_gate_width", "width_convoy", "width")
    widths = [w for op in openings if (w := _first_number(op, width_keys))]
    name = next((str(op["name"]) for op in openings if is_valid(op.get("name"))), "Bridge")
    return {
        "type": "bridge",
        "id": bridge_id,
        "name": name,
        "width_m": max(widths) if widths else None,
        "length_m": None,
        "passage_h": 0.0,
    }


def _require_files(paths: Iterable[pathlib.Path]) -> None:
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Required structure file {path} is missing.")


def _fis_section_key(record: Mapping[str, Any]) -> str | None:
    sid = record.get("fairway_section_id")
    name = str(record.get("name", ""))
    # Chamber and opening name variants of Irenesluis belong to FIS fairway section 47304.
    if "Irenesluis" in name or "Irenesluizen" in name:
        sid = 47304
    return str(int(float(sid))) if is_valid(sid) else None


def build_edge_structures_lookup(
    euris_dir: pathlib.Path | None = None,
    fis_dir: pathlib.Path | None = None,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Map graph edge keys to the locks and bridges on them.

    ``euris_dir`` and ``fis_dir`` are the detailed drop-in folders of fis-crawler,
    e.g. ``output/dropins-euris-detailed`` and ``output/dropins-fis-detailed``.
    """
    locks: defaultdict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    bridges: defaultdict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)

    if fis_dir is not None:
        ch_path, op_path = fis_dir / "chambers.geoparquet", fis_dir / "openings.geoparquet"
        _require_files((ch_path, op_path))
        for r in pd.read_parquet(ch_path).to_dict("records"):
            key = _fis_section_key(r)
            if key:
                lock_id = r.get("lock_id") if is_valid(r.get("lock_id")) else r.get("id")
                locks[key].setdefault(str(lock_id), []).append(r)
        for r in pd.read_parquet(op_path).to_dict("records"):
            key = _fis_section_key(r)
            if key:
                bridge_id = r.get("bridge_id") if is_valid(r.get("bridge_id")) else r.get("id")
                bridges[key].setdefault(str(bridge_id), []).append(r)

    if euris_dir is not None:
        edges_path = euris_dir / "edges.geoparquet"
        ch_path, op_path = euris_dir / "chambers.geoparquet", euris_dir / "openings.geoparquet"
        _require_files((edges_path, ch_path, op_path))
        chambers_by_lock: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pd.read_parquet(ch_path).to_dict("records"):
            if is_valid(r.get("lock_id")):
                chambers_by_lock[str(r["lock_id"])].append(r)
        openings_by_bridge: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pd.read_parquet(op_path).to_dict("records"):
            if is_valid(r.get("bridge_id")):
                openings_by_bridge[str(r["bridge_id"])].append(r)
        section_edges = pd.read_parquet(edges_path, columns=["section_id", "lock_id", "bridge_id"])
        for r in section_edges.to_dict("records"):
            if not is_valid(r["section_id"]):
                continue
            sid = str(r["section_id"])
            if is_valid(r["lock_id"]):
                locks[sid].setdefault(str(r["lock_id"]), chambers_by_lock[str(r["lock_id"])])
            if is_valid(r["bridge_id"]):
                bridges[sid].setdefault(
                    str(r["bridge_id"]), openings_by_bridge[str(r["bridge_id"])]
                )

    lookup: dict[str, tuple[dict[str, Any], ...]] = {}
    for key in set(locks) | set(bridges):
        lookup[key] = tuple(
            _lock_structure(lid, chs) for lid, chs in locks.get(key, {}).items()
        ) + tuple(_bridge_structure(bid, ops) for bid, ops in bridges.get(key, {}).items())
    logger.info(f"Built lock and bridge lookup for {len(lookup)} graph edges.")
    return lookup


# --------------------------------------------------------------- constraints


def _is_dutch(struct_id: Any) -> bool:
    if not struct_id:
        return True
    s_id = str(struct_id).upper()
    return s_id.isdigit() or s_id.startswith("FIS_") or "NL" in s_id


def _find_min_width(
    structures: Sequence[Mapping[str, Any]], d_edge: Mapping[str, Any]
) -> tuple[float | None, tuple[Any, str, str] | None]:
    """Return the narrowest passage on an edge and the structure that sets it."""
    min_width: float | None = None
    limiting: tuple[Any, str, str] | None = None
    for s in structures:
        if s["width_m"] is not None and (min_width is None or s["width_m"] < min_width):
            min_width = s["width_m"]
            limiting = (s["id"], s["name"], s["type"])

    edge_w = _number(d_edge.get("dim_structural_width"))
    if edge_w is None:
        edge_cm = _number(d_edge.get("mwidthcm"))
        edge_w = edge_cm / 100.0 if edge_cm is not None else None
    if edge_w is not None and (min_width is None or edge_w < min_width):
        min_width = edge_w
        limiting = (get_edge_key(d_edge), str(d_edge.get("name", "Fairway")), "fairway")
    return min_width, limiting


def check_edge_constraints_soft(
    d_edge: Mapping[str, Any],
    structures: Sequence[Mapping[str, Any]],
    ship_dims: Mapping[str, Any],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Evaluate the physical constraints of one edge for one ship.

    Returns soft penalties per category (plus ``total``) and the violations behind them.
    Uses ship ``beam``, ``length`` and ``cemt`` only. Bridge heights and water depths
    are not checked: both depend on water levels.
    """
    penalties = dict.fromkeys(PENALTY_KEYS, 0.0)
    violations: list[dict[str, Any]] = []

    min_width, limiting = _find_min_width(structures, d_edge)
    if limiting is not None and min_width is not None and ship_dims["beam"] > min_width:
        struct_id, struct_name, struct_type = limiting
        if _is_dutch(struct_id):
            penalties[struct_type] += DUTCH_WIDTH_PENALTY
        else:
            penalties[struct_type] += FOREIGN_WIDTH_PENALTY_PER_M * (ship_dims["beam"] - min_width)
        violations.append(
            {
                "type": "beam",
                "ship_value": ship_dims["beam"],
                "limit_value": min_width,
                "struct_id": struct_id,
                "struct_name": struct_name,
            }
        )

    lock_lengths = [s for s in structures if s["type"] == "lock" and s["length_m"] is not None]
    if lock_lengths:
        shortest = min(lock_lengths, key=lambda s: s["length_m"])
        if ship_dims["length"] > shortest["length_m"]:
            penalties["lock"] += LOCK_LENGTH_PENALTY
            violations.append(
                {
                    "type": "length",
                    "ship_value": ship_dims["length"],
                    "limit_value": shortest["length_m"],
                    "struct_id": shortest["id"],
                    "struct_name": shortest["name"],
                }
            )

    water_name = next(
        (str(d_edge[k]) for k in EDGE_WATER_NAME_ATTRS if is_valid(d_edge.get(k))), ""
    )
    if water_name.strip().casefold() in SEA_WATER_NAMES:
        penalties["sea"] += SEA_PENALTY
        violations.append(
            {
                "type": "sea",
                "ship_value": 1.0,
                "limit_value": 0.0,
                "struct_id": get_edge_key(d_edge),
                "struct_name": str(d_edge.get("name", "Sea Route")),
            }
        )

    edge_cemt = _edge_cemt_class(d_edge)
    if edge_cemt is not None and cemt_rank(ship_dims["cemt"]) > cemt_rank(edge_cemt):
        penalties["cemt"] += CEMT_PENALTY
        violations.append(
            {
                "type": "cemt",
                "ship_value": ship_dims["cemt"],
                "limit_value": edge_cemt,
                "struct_id": get_edge_key(d_edge),
                "struct_name": str(d_edge.get("name", "CEMT Class Constraint")),
            }
        )

    penalties["total"] = sum(penalties[k] for k in PENALTY_KEYS)
    return penalties, violations


def build_arc_travel_input(
    d_edge: Mapping[str, Any],
    structures: Sequence[Mapping[str, Any]],
    penalties: Mapping[str, float],
) -> ArcTravelInput:
    """Convert one edge, its locks and its soft penalties to cost-module travel inputs."""
    return ArcTravelInput(
        distance_km=float(d_edge[EDGE_LENGTH_M_ATTR]) / 1000.0,
        realised_speed_km_per_h=get_edge_speed_km_per_h(d_edge),
        lock_service_time_h=sum(s["passage_h"] for s in structures if s["type"] == "lock"),
        relaxation_penalty_time_h=float(penalties["total"]) * PENALTY_UNIT_TO_HOURS,
    )


# ------------------------------------------------------------------- voyages


def _to_geoseries(values: pd.Series, default_crs: str) -> gpd.GeoSeries:
    """Return point values (shapely, WKT or WKB) as a GeoSeries in ``SNAP_CRS``."""
    if isinstance(values, gpd.GeoSeries):
        series = values if values.crs is not None else values.set_crs(default_crs)
    else:
        sample = values.dropna()
        first = sample.iloc[0] if len(sample) else None
        if isinstance(first, str):
            series = gpd.GeoSeries.from_wkt(values, index=values.index, crs=default_crs)
        elif isinstance(first, (bytes, bytearray)):
            series = gpd.GeoSeries.from_wkb(values, index=values.index, crs=default_crs)
        else:
            series = gpd.GeoSeries(values, index=values.index, crs=default_crs)
    return series.to_crs(SNAP_CRS)


@dataclass
class EdgeSnapIndex:
    """Spatial index of graph edges in ``SNAP_CRS`` metres, in ``graph.edges`` order."""

    tree: STRtree
    from_nodes: list[Any]
    to_nodes: list[Any]
    from_xy: np.ndarray
    to_xy: np.ndarray


def build_edge_index(graph: nx.Graph) -> EdgeSnapIndex:
    """Index the edge lines of the graph (EPSG:4326) for nearest-edge snapping.

    Edges without a line geometry are indexed as a straight line between their nodes.
    """
    node_points = gpd.GeoSeries(
        {n: d["geometry"] for n, d in graph.nodes(data=True)}, crs="EPSG:4326"
    ).to_crs(SNAP_CRS)
    node_xy = dict(
        zip(node_points.index, zip(node_points.x, node_points.y, strict=True), strict=True)
    )

    from_nodes, to_nodes, lines = [], [], []
    for u, v, d in graph.edges(data=True):
        from_nodes.append(u)
        to_nodes.append(v)
        lines.append(d.get("geometry"))
    lines_3035 = gpd.GeoSeries(lines, crs="EPSG:4326").to_crs(SNAP_CRS).to_numpy()
    for i, line in enumerate(lines_3035):
        if line is None or line.is_empty:
            lines_3035[i] = LineString([node_xy[from_nodes[i]], node_xy[to_nodes[i]]])

    return EdgeSnapIndex(
        tree=STRtree(lines_3035),
        from_nodes=from_nodes,
        to_nodes=to_nodes,
        from_xy=np.array([node_xy[n] for n in from_nodes]),
        to_xy=np.array([node_xy[n] for n in to_nodes]),
    )


def _snap(
    points: gpd.GeoSeries, index: EdgeSnapIndex
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Snap points to the nearest edge, then to the nearer of that edge's two nodes.

    Returns the node, the edge index, the distance to the edge and the distance to the node.
    """
    valid = (points.notna() & ~points.is_empty).to_numpy()
    nodes = np.full(len(points), None, dtype=object)
    edges = np.full(len(points), -1, dtype=np.int64)
    edge_dist = np.full(len(points), np.nan)
    node_dist = np.full(len(points), np.nan)
    if valid.any():
        geoms = points[valid].to_numpy()
        (point_idx, edge_idx), dist = index.tree.query_nearest(
            geoms, return_distance=True, all_matches=False
        )
        xy = np.column_stack([shapely.get_x(geoms), shapely.get_y(geoms)])[point_idx]
        d_from = np.hypot(*(xy - index.from_xy[edge_idx]).T)
        d_to = np.hypot(*(xy - index.to_xy[edge_idx]).T)
        use_from = d_from <= d_to

        rows = np.flatnonzero(valid)[point_idx]
        nodes[rows] = [
            index.from_nodes[e] if f else index.to_nodes[e]
            for e, f in zip(edge_idx, use_from, strict=True)
        ]
        edges[rows] = edge_idx
        edge_dist[rows] = dist
        node_dist[rows] = np.where(use_from, d_from, d_to)
    return nodes, edges, edge_dist, node_dist


def _ship_dims_or_error(
    sk_code: Any, cargo_t: float, capacity_t: float, dtv_db: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if not is_valid(sk_code) or not str(sk_code).strip():
        return None, "missing vessel class (SK_CODE)"
    try:
        dims = get_ship_dimensions(str(sk_code), cargo_t, capacity_t, dtv_db)
        dims["cemt"] = normalise_cemt_class(str(dims["cemt"]))
    except (ValidationError, AttributeError) as error:
        return None, f"unsupported vessel class {sk_code!r}: {error}"
    return dims, None


def _routing_ratio(cargo_t: float, capacity_t: float, step: float | None) -> float | None:
    """Loading ratio used for route choice, optionally rounded to multiples of ``step``."""
    try:
        ratio = _loading_ratio(cargo_t, None if np.isnan(capacity_t) else capacity_t)
    except ValidationError:
        return None
    if step and ratio > 0.0:
        ratio = min(1.0, max(step, round(ratio / step) * step))
    return ratio


def prepare_voyages(
    trips: pd.DataFrame,
    edge_index: EdgeSnapIndex,
    dtv_db: dict[str, Any],
    metric: RouteCostMetric,
    *,
    points_crs: str = IVS_POINTS_CRS,
    max_snap_distance_m: float | None = None,
    routing_ratio_step: float | None = None,
) -> pd.DataFrame:
    """Snap IVS voyages to graph nodes and derive their vessel and routing inputs.

    ``trips`` is a DataFrame or GeoDataFrame in the format of ``ivs_trips_extended_*.csv``.
    Point columns may hold shapely points, WKT or WKB; without a CRS they are read
    in ``points_crs``. Each point is snapped to the nearest edge, and then to the nearer
    of that edge's two nodes; ``*_snap_m`` is the distance to the edge and
    ``max_snap_distance_m`` applies to it. A missing ``Lading`` is an empty trip.
    ``routing_ratio_step`` rounds the loading ratio used for route choice (see
    :func:`assign_traffic`).
    """
    missing = [c for c in REQUIRED_IVS_COLUMNS if c not in trips.columns]
    if missing:
        raise ValueError(f"IVS trips are missing column(s): {missing}")
    duplicates = int(trips["Pseudo_ReisID"].duplicated().sum())
    if duplicates:
        logger.warning(
            f"{duplicates} duplicate Pseudo_ReisID values; voyage_index tells them apart."
        )

    v = pd.DataFrame(
        {
            "voyage_index": np.arange(len(trips)),
            "pseudo_reis_id": trips["Pseudo_ReisID"].to_numpy(),
            "sk_code": trips["SK_CODE"].to_numpy(),
            "segment": trips["Segment"].to_numpy(),
            "cargo_t": pd.to_numeric(trips["Lading"], errors="coerce").fillna(0.0).to_numpy(),
            "capacity_t": pd.to_numeric(trips["Laadvermogen"], errors="coerce").to_numpy(),
        }
    )
    for src, dst in OPTIONAL_IVS_COLUMNS.items():
        if src in trips.columns:
            v[dst] = trips[src].to_numpy()
    v["appearance"] = v["segment"].map(SEGMENT_APPEARANCE)
    v["is_loaded"] = v["cargo_t"] > 0

    for prefix, col in (("origin", ORIGIN_GEOM_COL), ("destination", DESTINATION_GEOM_COL)):
        points = _to_geoseries(trips[col].reset_index(drop=True), points_crs)
        (
            v[f"{prefix}_node"],
            v[f"{prefix}_snap_edge_index"],
            v[f"{prefix}_snap_m"],
            v[f"{prefix}_node_dist_m"],
        ) = _snap(points, edge_index)

    v["status"] = STATUS_PENDING
    v["detail"] = None
    no_point = v["origin_node"].isna() | v["destination_node"].isna()
    v.loc[no_point, "status"] = STATUS_SNAP_FAIL
    v.loc[no_point, "detail"] = "missing origin or destination point"
    if max_snap_distance_m is not None:
        too_far = ~no_point & (
            (v["origin_snap_m"] > max_snap_distance_m)
            | (v["destination_snap_m"] > max_snap_distance_m)
        )
        v.loc[too_far, "status"] = STATUS_SNAP_FAIL
        v.loc[too_far, "detail"] = f"point further than {max_snap_distance_m} m from the graph"

    dims_cache: dict[tuple[Any, float, float], tuple[dict[str, Any] | None, str | None]] = {}
    dims_rows, errors = [], []
    for sk_code, cargo_t, capacity_t in zip(
        v["sk_code"], v["cargo_t"], v["capacity_t"], strict=False
    ):
        key = (sk_code, cargo_t, capacity_t)
        if key not in dims_cache:
            dims_cache[key] = _ship_dims_or_error(sk_code, cargo_t, capacity_t, dtv_db)
        dims, error = dims_cache[key]
        dims_rows.append(dims or {})
        errors.append(error)
    v["beam"] = [d.get("beam") for d in dims_rows]
    v["length"] = [d.get("length") for d in dims_rows]
    v["draft"] = [d.get("draft") for d in dims_rows]
    v["ship_cemt"] = [d.get("cemt") for d in dims_rows]
    vessel_fail = (v["status"] == STATUS_PENDING) & pd.Series(errors).notna().to_numpy()
    v.loc[vessel_fail, "status"] = STATUS_VESSEL_FAIL
    v.loc[vessel_fail, "detail"] = pd.Series(errors)[vessel_fail.to_numpy()].to_numpy()

    v["routing_loading_ratio"] = [
        _routing_ratio(c, cap, routing_ratio_step)
        for c, cap in zip(v["cargo_t"], v["capacity_t"], strict=False)
    ]

    # Voyages with the same routing key get the same edge weights. The ship part of the
    # key holds the dimensions read by check_edge_constraints_soft.
    keys: list[tuple[Any, ...]] = []
    for row in v.itertuples(index=False):
        key: tuple[Any, ...] = (row.beam, row.length, row.ship_cemt)
        if metric is RouteCostMetric.TRAVEL_COST_EUR:
            key += (row.sk_code, row.appearance, row.routing_loading_ratio)
        keys.append(key)
    v["routing_key_id"] = pd.factorize(pd.Series(keys, dtype=object))[0]

    logger.info(f"Prepared {len(v)} voyages: {v['status'].value_counts().to_dict()}")
    return v


def build_voyage_cost_model(
    cost_config: RoutingCostConfig,
    sk_code: str,
    appearance: str | None,
    cargo_t: float,
    capacity_t: float | None,
    loading_ratio: float | None = None,
) -> VoyageCostModel:
    """Build the cost model of one voyage, with the appearance taken from its IVS segment.

    ``loading_ratio`` overrides the ratio derived from cargo and capacity.
    """
    if not isinstance(appearance, str) or not appearance:
        raise ValidationError("No cargo appearance for the voyage's IVS segment")
    if loading_ratio is None:
        loading_ratio = _loading_ratio(cargo_t, capacity_t)
    return VoyageCostModel(
        metric=cost_config.metric,
        rates=select_cost_rates(cost_config.rates, appearance, sk_code),
        scenario=cost_config.scenario,
        fuel_provider=cost_config.fuel_provider,
        vessel_rws_class=sk_code,
        vessel_profile=profile_for_appearance(appearance),
        loading_ratio=loading_ratio,
        trip=TripCostInput(cargo_t=cargo_t, is_loaded=cargo_t > 0),
        appearance_source=SEGMENT_APPEARANCE_SOURCE,
    )


# ------------------------------------------------------------------- routing


@dataclass
class _EdgeProfile:
    """Per-edge travel inputs, penalties and fuel classes for one set of ship dimensions."""

    arcs: list[ArcTravelInput]
    penalties: list[dict[str, float]]
    violations: list[list[dict[str, Any]]]
    fuel_cemts: list[str]


def _edge_profile(
    edges: Sequence[tuple[Any, Any, dict[str, Any]]],
    lookup: Mapping[str, tuple[dict[str, Any], ...]],
    ship_dims: Mapping[str, Any],
) -> _EdgeProfile:
    profile = _EdgeProfile([], [], [], [])
    for _, _, d in edges:
        key = get_edge_key(d)
        structures = lookup.get(key, ()) if key else ()
        pen, viol = check_edge_constraints_soft(d, structures, ship_dims)
        profile.arcs.append(build_arc_travel_input(d, structures, pen))
        profile.penalties.append(pen)
        profile.violations.append(viol)
        profile.fuel_cemts.append(_fuel_cemt_class(d, ship_dims["cemt"]))
    return profile


def _edge_weights(
    profile: _EdgeProfile, cost_model: VoyageCostModel | None, metric: RouteCostMetric
) -> list[float]:
    """Route-search weight of every edge, from the package cost calculators."""
    if metric is RouteCostMetric.TRAVEL_TIME:
        return [calculate_arc_choice_time_h(arc) for arc in profile.arcs]
    if cost_model is None:
        raise ValidationError("A cost model is required to route on travel cost")
    fuels = [cost_model.fuel_for(c) for c in profile.fuel_cemts]
    costs = calculate_arc_costs(profile.arcs, cost_model.rates, cost_model.scenario, fuels)
    return [arc.choice_cost_eur for arc in costs]


_TRIP_COST_FIELDS = (
    "reported_total_cost_eur",
    "choice_total_cost_eur",
    "fixed_cost_eur",
    "reported_time_h",
    "fuel_l",
    "appearance_source",
    "loading_ratio",
)


def _trip_cost_fields(pricing: RoutePricing | None) -> dict[str, Any]:
    if pricing is None:
        return dict.fromkeys(_TRIP_COST_FIELDS)
    return {
        "reported_total_cost_eur": pricing.trip.reported_total_cost_eur,
        "choice_total_cost_eur": pricing.trip.choice_total_cost_eur,
        "fixed_cost_eur": pricing.trip.fixed_cost_eur,
        "reported_time_h": pricing.reported_time_h,
        "fuel_l": pricing.fuel_l,
        "appearance_source": pricing.appearance_source,
        "loading_ratio": pricing.loading_ratio,
    }


def _path_to(pred: Mapping[Any, list[Any]], origin: Any, destination: Any) -> list[Any]:
    path = [destination]
    while path[-1] != origin:
        path.append(pred[path[-1]][0])
    path.reverse()
    return path


_PENALTY_COLUMNS = tuple(f"penalty_{k}" for k in (*PENALTY_KEYS, "total"))
_EDGE_ROW_COLUMNS = (
    "voyage_index",
    "pseudo_reis_id",
    "seq",
    "edge_index",
    "edge_id",
    "from_node",
    "to_node",
    "cargo_t",
    "is_loaded",
    *_PENALTY_COLUMNS,
    "reported_time_h",
    "reported_cost_eur",
    "choice_cost_eur",
    "fuel_l",
)


def route_voyage_batch(
    batch: pd.DataFrame,
    graph: nx.Graph,
    lookup: Mapping[str, tuple[dict[str, Any], ...]],
    cost_config: RoutingCostConfig,
) -> tuple[list[dict[str, Any]], pd.DataFrame, list[dict[str, Any]]]:
    """Route a batch of prepared voyages; returns voyage records, voyage edges and violations.

    One Dijkstra search runs per routing key and origin node.
    """
    metric = cost_config.metric
    edges = list(graph.edges(data=True))
    edge_pos = {id(d): i for i, (_, _, d) in enumerate(edges)}
    edge_keys = [get_edge_key(d) for _, _, d in edges]
    profiles: dict[tuple[Any, ...], _EdgeProfile] = {}
    models: dict[tuple[Any, ...], VoyageCostModel | str] = {}

    def cost_model_for(
        row: Any, loading_ratio: float | None = None
    ) -> tuple[VoyageCostModel | None, str | None]:
        capacity = None if np.isnan(row.capacity_t) else float(row.capacity_t)
        key = (row.sk_code, row.appearance, row.cargo_t, capacity, loading_ratio)
        if key not in models:
            try:
                models[key] = build_voyage_cost_model(
                    cost_config,
                    str(row.sk_code),
                    row.appearance,
                    float(row.cargo_t),
                    capacity,
                    loading_ratio,
                )
            except IWWRouteAssignmentError as error:
                models[key] = str(error)
        model = models[key]
        return (None, model) if isinstance(model, str) else (model, None)

    records: list[dict[str, Any]] = []
    edge_rows: dict[str, list[Any]] = {c: [] for c in _EDGE_ROW_COLUMNS}
    violations: list[dict[str, Any]] = []

    def base_record(row: Any) -> dict[str, Any]:
        rec = {k: val for k, val in row._asdict().items() if k != "routing_key_id"}
        rec["route_metric"] = metric.value
        return rec

    for _, key_rows in batch.groupby("routing_key_id", sort=False):
        first = next(key_rows.itertuples(index=False))
        dims = {"beam": first.beam, "length": first.length, "cemt": first.ship_cemt}
        dims_key = (first.beam, first.length, first.ship_cemt)
        if dims_key not in profiles:
            profiles[dims_key] = _edge_profile(edges, lookup, dims)
        profile = profiles[dims_key]

        routing_model, routing_error = (
            cost_model_for(first, first.routing_loading_ratio)
            if metric is RouteCostMetric.TRAVEL_COST_EUR
            else (None, None)
        )
        try:
            weights = _edge_weights(profile, routing_model, metric)
        except IWWRouteAssignmentError as error:
            for row in key_rows.itertuples(index=False):
                records.append(
                    base_record(row)
                    | {"status": STATUS_COST_FAIL, "detail": routing_error or str(error)}
                )
            continue

        def weight(u: Any, v: Any, d: dict[str, Any], w: list[float] = weights) -> float:
            return w[edge_pos[id(d)]]

        for origin, origin_rows in key_rows.groupby("origin_node", sort=False):
            try:
                pred, dist = nx.dijkstra_predecessor_and_distance(graph, origin, weight=weight)
            except nx.NodeNotFound:
                pred, dist = {}, {}

            for row in origin_rows.itertuples(index=False):
                rec = base_record(row)
                if row.destination_node not in dist:
                    records.append(rec | {"status": STATUS_NO_PATH, "detail": "no path in graph"})
                    continue

                path = _path_to(pred, origin, row.destination_node)
                idxs = [
                    edge_pos[id(graph[u][v])] for u, v in zip(path[:-1], path[1:], strict=False)
                ]

                cost_model, cost_error = cost_model_for(row)
                pricing = None
                if cost_model is not None:
                    try:
                        pricing = cost_model.price_route(
                            [profile.arcs[i] for i in idxs], [profile.fuel_cemts[i] for i in idxs]
                        )
                    except IWWRouteAssignmentError as error:
                        cost_error = str(error)
                if pricing is None and metric is RouteCostMetric.TRAVEL_COST_EUR:
                    records.append(rec | {"status": STATUS_COST_FAIL, "detail": cost_error})
                    continue

                n_violations = 0
                for seq, (i, u, v) in enumerate(zip(idxs, path[:-1], path[1:], strict=False)):
                    arc_cost = pricing.arcs[seq] if pricing is not None else None
                    edge_rows["voyage_index"].append(row.voyage_index)
                    edge_rows["pseudo_reis_id"].append(row.pseudo_reis_id)
                    edge_rows["seq"].append(seq)
                    edge_rows["edge_index"].append(i)
                    edge_rows["edge_id"].append(edge_keys[i])
                    edge_rows["from_node"].append(u)
                    edge_rows["to_node"].append(v)
                    edge_rows["cargo_t"].append(row.cargo_t)
                    edge_rows["is_loaded"].append(row.is_loaded)
                    for k in (*PENALTY_KEYS, "total"):
                        edge_rows[f"penalty_{k}"].append(profile.penalties[i][k])
                    edge_rows["reported_time_h"].append(
                        arc_cost.reported_time_h if arc_cost else None
                    )
                    edge_rows["reported_cost_eur"].append(
                        arc_cost.reported_variable_cost_eur if arc_cost else None
                    )
                    edge_rows["choice_cost_eur"].append(
                        arc_cost.choice_cost_eur if arc_cost else None
                    )
                    edge_rows["fuel_l"].append(
                        arc_cost.sailing_fuel_l + arc_cost.idle_fuel_l if arc_cost else None
                    )
                    for viol in profile.violations[i]:
                        n_violations += 1
                        violations.append(
                            {
                                "voyage_index": row.voyage_index,
                                "pseudo_reis_id": row.pseudo_reis_id,
                                "edge_index": i,
                                "edge_id": edge_keys[i],
                                "violation_type": viol["type"],
                                "ship_class": row.sk_code,
                                "ship_value": viol["ship_value"],
                                "limit_value": viol["limit_value"],
                                "struct_id": viol["struct_id"],
                                "struct_name": viol["struct_name"],
                                "cargo_t": row.cargo_t,
                            }
                        )

                records.append(
                    rec
                    | {
                        "status": STATUS_SUCCESS,
                        "detail": None,
                        "n_edges": len(idxs),
                        "distance_km": sum(profile.arcs[i].distance_km for i in idxs),
                        "route_weight": dist[row.destination_node],
                        "violation_count": n_violations,
                        "cost_error": cost_error,
                    }
                    | _trip_cost_fields(pricing)
                )

    voyage_edges = pd.DataFrame(edge_rows, columns=list(_EDGE_ROW_COLUMNS))
    for col in ("reported_time_h", "reported_cost_eur", "choice_cost_eur", "fuel_l"):
        voyage_edges[col] = voyage_edges[col].astype(float)
    return records, voyage_edges, violations


def _chunk_voyages(pending: pd.DataFrame, chunk_size: int) -> list[pd.DataFrame]:
    """Split voyages into batches without splitting a routing-key/origin group."""
    if pending.empty:
        return []
    origin_id = pd.factorize(pending["origin_node"])[0]
    group = (
        pd.DataFrame({"k": pending["routing_key_id"].to_numpy(), "o": origin_id})
        .groupby(["k", "o"], sort=True)
        .ngroup()
        .to_numpy()
    )
    order = np.argsort(group, kind="stable")
    pending, group = pending.iloc[order], group[order]
    chunk_of_group = (np.cumsum(np.bincount(group)) - 1) // chunk_size
    chunk = chunk_of_group[group]
    return [pending[chunk == c] for c in np.unique(chunk)]


def run_routing(
    voyages: pd.DataFrame,
    graph: nx.Graph,
    lookup: Mapping[str, tuple[dict[str, Any], ...]],
    cost_config: RoutingCostConfig,
    *,
    n_workers: int = 4,
    chunk_size: int = 2000,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Route all pending voyages, in parallel with Dask when ``n_workers > 1``.

    Returns the voyage table (all voyages, with status), the voyage-edge table and
    the violations table.
    """
    pending = voyages[voyages["status"] == STATUS_PENDING]
    rejected = voyages[voyages["status"] != STATUS_PENDING]
    chunks = _chunk_voyages(pending, chunk_size)
    logger.info(f"Routing {len(pending)} voyages in {len(chunks)} batches...")

    if n_workers > 1 and len(chunks) > 1:
        from dask.distributed import Client, LocalCluster

        with (
            LocalCluster(n_workers=n_workers, threads_per_worker=1) as cluster,
            Client(cluster) as client,
        ):
            logger.info(f"Dask dashboard available at: {client.dashboard_link}")
            g_f, lookup_f, cfg_f = client.scatter([graph, lookup, cost_config], broadcast=True)
            futures = [
                client.submit(route_voyage_batch, chunk, g_f, lookup_f, cfg_f) for chunk in chunks
            ]
            batch_results = client.gather(futures)
    else:
        batch_results = [route_voyage_batch(chunk, graph, lookup, cost_config) for chunk in chunks]

    records = [r for res in batch_results for r in res[0]]
    records += [
        {k: val for k, val in r.items() if k != "routing_key_id"}
        | {"route_metric": cost_config.metric.value}
        for r in rejected.to_dict("records")
    ]
    voyage_table = pd.DataFrame(records).sort_values("voyage_index").reset_index(drop=True)
    edge_frames = [res[1] for res in batch_results if not res[1].empty]
    voyage_edges = (
        pd.concat(edge_frames, ignore_index=True)
        if edge_frames
        else pd.DataFrame(columns=list(_EDGE_ROW_COLUMNS))
    )
    violations = pd.DataFrame([v for res in batch_results for v in res[2]])

    logger.info(f"Routing complete: {voyage_table['status'].value_counts().to_dict()}")
    return voyage_table, voyage_edges, violations


# --------------------------------------------------------------- aggregation


def edge_table(graph: nx.Graph) -> gpd.GeoDataFrame:
    """One row per graph edge, in ``graph.edges`` order (``edge_index``)."""
    rows = [
        {
            "edge_index": i,
            "edge_id": get_edge_key(d),
            "from_node": u,
            "to_node": v,
            "name": d.get("name", "Unnamed"),
            "data_source": d.get("data_source", "unknown"),
            "cemt_class": _edge_cemt_class(d),
            "length_km": float(d[EDGE_LENGTH_M_ATTR]) / 1000.0,
            "geometry": d.get("geometry"),
        }
        for i, (u, v, d) in enumerate(graph.edges(data=True))
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def aggregate_intensity(voyage_edges: pd.DataFrame, edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sum trips, cargo, penalties and costs per edge."""
    ve = voyage_edges.assign(is_empty=~voyage_edges["is_loaded"].astype(bool))
    agg = ve.groupby("edge_index").agg(
        trip_count=("voyage_index", "size"),
        trip_count_empty=("is_empty", "sum"),
        trip_count_costed=("reported_cost_eur", "count"),
        cargo_t=("cargo_t", "sum"),
        **{col: (col, "sum") for col in _PENALTY_COLUMNS},
        reported_cost_eur=("reported_cost_eur", "sum"),
        choice_cost_eur=("choice_cost_eur", "sum"),
        fuel_l=("fuel_l", "sum"),
        reported_time_h=("reported_time_h", "sum"),
    )
    return edges.merge(agg, left_on="edge_index", right_index=True, how="inner")


def aggregate_penalized(violations: pd.DataFrame, edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Summarise constraint violations per edge and violation type."""
    if violations.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    agg = (
        violations.groupby(["edge_index", "violation_type"])
        .agg(
            trips=("voyage_index", "nunique"),
            cargo_t=("cargo_t", "sum"),
            ship_classes=("ship_class", lambda s: ",".join(sorted(map(str, s.unique())))),
            limit_value=("limit_value", "first"),
            struct_id=("struct_id", "first"),
            struct_name=("struct_name", "first"),
        )
        .reset_index()
    )
    agg["limit_value"] = agg["limit_value"].astype(str)
    meta = edges[["edge_index", "edge_id", "name", "data_source", "geometry"]]
    return gpd.GeoDataFrame(agg.merge(meta, on="edge_index"), geometry="geometry", crs="EPSG:4326")


@dataclass
class AssignmentResult:
    """Tables produced by :func:`assign_traffic`."""

    voyages: pd.DataFrame
    voyage_edges: pd.DataFrame
    intensity: gpd.GeoDataFrame
    penalized: gpd.GeoDataFrame


def export_assignment_outputs(output_dir: pathlib.Path, result: AssignmentResult) -> None:
    """Write voyages, voyage edges, edge intensities and penalized edges."""
    if result.intensity.empty:
        raise ValueError("Assignment outputs are empty. No voyages were routed successfully.")

    result.voyages.to_parquet(output_dir / "voyages.parquet")
    result.voyage_edges.to_parquet(output_dir / "voyage_edges.parquet")
    result.intensity.to_parquet(output_dir / "intensity.geoparquet")
    result.intensity.to_file(output_dir / "intensity.geojson", driver="GeoJSON")

    gpkg_path = output_dir / "routing_detailed_analysis.gpkg"
    result.intensity.to_file(gpkg_path, layer="connected_routes", driver="GPKG")
    if not result.penalized.empty:
        result.penalized.to_file(gpkg_path, layer="penalized_edges", driver="GPKG")
    logger.info(f"Saved assignment outputs to {output_dir}.")


# ---------------------------------------------------------------------- main


def assign_traffic(
    trips: pd.DataFrame,
    graph_path: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    cost_rates_path: pathlib.Path,
    fuel_observations_path: pathlib.Path,
    vessel_reference_path: pathlib.Path,
    scenario: ScenarioCostParameters,
    metric: RouteCostMetric = RouteCostMetric.TRAVEL_TIME,
    reference_dir: pathlib.Path = pathlib.Path("reference"),
    euris_structures_dir: pathlib.Path | None = None,
    fis_structures_dir: pathlib.Path | None = None,
    points_crs: str = IVS_POINTS_CRS,
    max_snap_distance_m: float | None = None,
    routing_ratio_step: float | None = None,
    n_workers: int = 4,
    chunk_size: int = 2000,
) -> AssignmentResult:
    """Assign IVS voyages to the graph and report travel time, cost and fuel per voyage.

    ``trips`` is a DataFrame or GeoDataFrame with one voyage per row, in the format of
    ``ivs_trips_extended_*.csv``. ``metric`` selects what the route search minimises.
    ``reference_dir`` holds ``DTV_shiptypes_database.json``. Without structure folders,
    locks and bridges are not checked and lock passage time is zero.

    With the EUR metric, nearly every voyage has its own loading ratio and so needs its
    own route search. ``routing_ratio_step`` (e.g. 0.05) rounds the ratio used for route
    choice only, so voyages can share searches; reported costs use the exact ratio.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    metric = RouteCostMetric(metric)

    cost_config = RoutingCostConfig(
        metric=metric,
        rates=load_cost_rates_csv(cost_rates_path),
        fuel_provider=load_fuel_provider(fuel_observations_path, vessel_reference_path),
        scenario=scenario,
    )
    dtv_db = load_shiptypes(reference_dir)
    graph = load_graph(graph_path)
    if euris_structures_dir is None and fis_structures_dir is None:
        logger.warning("No structure folders given; locks and bridges are not checked.")
    lookup = build_edge_structures_lookup(euris_structures_dir, fis_structures_dir)

    voyages = prepare_voyages(
        trips,
        build_edge_index(graph),
        dtv_db,
        metric,
        points_crs=points_crs,
        max_snap_distance_m=max_snap_distance_m,
        routing_ratio_step=routing_ratio_step,
    )
    voyage_table, voyage_edges, violations = run_routing(
        voyages, graph, lookup, cost_config, n_workers=n_workers, chunk_size=chunk_size
    )

    edges = edge_table(graph)
    result = AssignmentResult(
        voyages=voyage_table,
        voyage_edges=voyage_edges,
        intensity=aggregate_intensity(voyage_edges, edges),
        penalized=aggregate_penalized(violations, edges),
    )
    export_assignment_outputs(output_dir, result)
    logger.info("Traffic assignment complete!")
    return result
