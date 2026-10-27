"""EuRIS adapter: build the routing network and its physical limits from fis-crawler outputs.

Inputs:

- ``euris-enriched/graph.pickle``: an undirected networkx ``Graph``. Nodes carry a
  ``geometry`` point in EPSG:4326. Edges carry ``length_m``, ``code``, ``geometry``, speeds,
  ``cemt``, ``ww_name`` and the section limits ``mwidthcm``, ``mlengthcm`` and ``mdraughtcm``.
- ``dropins-euris-detailed``: ``edges.geoparquet`` links sections to locks and bridges.
  ``chambers.geoparquet`` and ``openings.geoparquet`` hold their dimensions.

Every limit is read with an explicit unit and recorded with its source column.
"""

from __future__ import annotations

import logging
import pickle
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from ..assignment.network import AssignmentNetwork, NetworkEdge
from ..domain.classifications import cemt_rank, normalise_cemt_class
from ..domain.enums import StructureType
from ..domain.navigability import ArcRestriction
from ..exceptions import ValidationError
from ._frames import require_columns

logger = logging.getLogger(__name__)

SPEED_ATTRIBUTES = ("maxspeed_up", "maxspeed_down", "maxspeed", "calspeed_up", "calspeed_down")
DEFAULT_SPEED_KM_PER_H = 10.0
MIN_SPEED_KM_PER_H = 1.0
WATER_NAME_ATTRIBUTES = ("water_name", "ww_name")

STRUCTURE_FILES = {
    "edges.geoparquet": ("section_id", "lock_id", "bridge_id"),
    "chambers.geoparquet": ("lock_id", "dim_gate_width", "dim_usable_length", "mdraughtcm"),
    "openings.geoparquet": (
        "bridge_id",
        "dim_gate_width",
        "dim_height",
        "cl_height",
        "mheightcmc",
        "operable",
    ),
}
NAME_FILES = {"locks.geoparquet": "lock", "bridges.geoparquet": "bridge"}


def _number(value: Any) -> float | None:
    """Return a finite number from a value such as 12, '12.0' or '12 km/h'."""
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return None
    if isinstance(value, str):
        match = re.search(r"[-+]?\d*\.\d+|\d+", value)
        if not match:
            return None
        value = match.group(0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _positive(value: Any, scale: float = 1.0) -> float | None:
    number = _number(value)
    return number * scale if number is not None and number > 0 else None


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return ""
    return str(value).strip()


@dataclass(frozen=True, slots=True)
class EurisLimitRules:
    """Choices made when turning EuRIS records into network inputs.

    ``lock_passage_time_h`` is reported time per lock on a section, added to travel time.
    ``fairway_length`` adds the section ``mlengthcm`` as a length limit. It is off by default
    because the length check has so far applied to locks only.
    """

    lock_passage_time_h: float = 0.5
    fairway_length: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.lock_passage_time_h) or self.lock_passage_time_h < 0:
            raise ValidationError("lock_passage_time_h must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class LockLimits:
    """Largest vessel a lock takes: its widest, longest and deepest chamber."""

    lock_id: str
    name: str
    max_beam_m: float | None
    max_length_m: float | None
    max_draught_m: float | None
    chamber_count: int


@dataclass(frozen=True, slots=True)
class BridgeLimits:
    """Largest vessel a bridge takes. A bridge with a movable opening has no height limit."""

    bridge_id: str
    name: str
    max_beam_m: float | None
    max_air_draught_m: float | None
    opening_count: int
    movable: bool


@dataclass(frozen=True, slots=True, eq=False)
class EurisStructures:
    """Locks and bridges, and which sections they sit on."""

    locks: Mapping[str, LockLimits]
    bridges: Mapping[str, BridgeLimits]
    section_locks: Mapping[str, tuple[str, ...]]
    section_bridges: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True, eq=False)
class EurisInputs:
    """Network, physical limits per arc, and a report of what was read and skipped."""

    network: AssignmentNetwork
    restrictions: tuple[ArcRestriction, ...]
    report: Mapping[str, Any] = field(default_factory=dict)


def load_euris_graph(path: str | Path) -> nx.Graph:
    """Load the enriched EuRIS graph. Only load pickle files from a trusted source."""
    with Path(path).open("rb") as stream:
        graph = pickle.load(stream)  # noqa: S301 - trusted fis-crawler output
    if not isinstance(graph, nx.Graph) or graph.is_multigraph() or graph.is_directed():
        raise ValidationError(
            f"{path} must hold an undirected networkx Graph; received {type(graph).__name__}. "
            "Use euris-enriched/graph.pickle, not the detailed MultiGraph."
        )
    return graph


def _read_table(directory: Path, name: str, columns: Iterable[str]) -> pd.DataFrame:
    path = directory / name
    if not path.is_file():
        raise ValidationError(f"Required EuRIS file {path} is missing")
    table = pd.read_parquet(path)
    require_columns(table, set(columns), name)
    return table


def _names(directory: Path, name: str) -> dict[str, str]:
    path = directory / name
    if not path.is_file():
        return {}
    table = pd.read_parquet(path, columns=["id", "name"])
    return {_text(i): _text(n) for i, n in zip(table["id"], table["name"], strict=True) if _text(i)}


def _records(table: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): v for k, v in row.items()} for row in table.to_dict("records")]


def _max(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def build_euris_structures(
    section_links: pd.DataFrame,
    chambers: pd.DataFrame,
    openings: pd.DataFrame,
    *,
    lock_names: Mapping[str, str] | None = None,
    bridge_names: Mapping[str, str] | None = None,
) -> EurisStructures:
    """Summarise locks and bridges and link them to sections.

    Lock limits are the widest gate (``dim_gate_width``, m), the longest usable length
    (``dim_usable_length``, m) and the deepest draught (``mdraughtcm``, cm) of its chambers.
    Bridge width is the widest opening (``dim_gate_width``, m). A bridge with an operable
    opening has no height limit. Otherwise its height is the highest opening, from
    ``dim_height`` (m), else ``cl_height`` (cm), else ``mheightcmc`` (cm). Zero and
    missing values are not limits.
    """
    lock_names = lock_names or {}
    bridge_names = bridge_names or {}
    chambers_by_lock: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _records(chambers):
        if _text(row["lock_id"]):
            chambers_by_lock[_text(row["lock_id"])].append(row)
    locks = {
        lock_id: LockLimits(
            lock_id,
            lock_names.get(lock_id, ""),
            _max(_positive(r["dim_gate_width"]) for r in rows),
            _max(_positive(r["dim_usable_length"]) for r in rows),
            _max(_positive(r["mdraughtcm"], 0.01) for r in rows),
            len(rows),
        )
        for lock_id, rows in sorted(chambers_by_lock.items())
    }
    openings_by_bridge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _records(openings):
        if _text(row["bridge_id"]):
            openings_by_bridge[_text(row["bridge_id"])].append(row)
    bridges = {}
    for bridge_id, rows in sorted(openings_by_bridge.items()):
        movable = any(_number(r["operable"]) == 1 for r in rows)
        heights = (
            _positive(r["dim_height"])
            or _positive(r["cl_height"], 0.01)
            or _positive(r["mheightcmc"], 0.01)
            for r in rows
        )
        bridges[bridge_id] = BridgeLimits(
            bridge_id,
            bridge_names.get(bridge_id, ""),
            _max(_positive(r["dim_gate_width"]) for r in rows),
            None if movable else _max(heights),
            len(rows),
            movable,
        )
    section_locks: dict[str, set[str]] = defaultdict(set)
    section_bridges: dict[str, set[str]] = defaultdict(set)
    for row in _records(section_links):
        section = _text(row["section_id"])
        if not section:
            continue
        lock_id = _text(row["lock_id"])
        if lock_id:
            # A linked lock without chamber data still costs passage time, with no limits.
            locks.setdefault(
                lock_id, LockLimits(lock_id, lock_names.get(lock_id, ""), None, None, None, 0)
            )
            section_locks[section].add(lock_id)
        if _text(row["bridge_id"]) in bridges:
            section_bridges[section].add(_text(row["bridge_id"]))
    return EurisStructures(
        MappingProxyType(dict(sorted(locks.items()))),
        MappingProxyType(bridges),
        MappingProxyType({k: tuple(sorted(v)) for k, v in section_locks.items()}),
        MappingProxyType({k: tuple(sorted(v)) for k, v in section_bridges.items()}),
    )


def load_euris_structures(directory: str | Path) -> EurisStructures:
    """Read the detailed EuRIS drop-ins: section links, lock chambers and bridge openings."""
    folder = Path(directory)
    tables = {name: _read_table(folder, name, cols) for name, cols in STRUCTURE_FILES.items()}
    return build_euris_structures(
        tables["edges.geoparquet"],
        tables["chambers.geoparquet"],
        tables["openings.geoparquet"],
        lock_names=_names(folder, "locks.geoparquet"),
        bridge_names=_names(folder, "bridges.geoparquet"),
    )


def _speed(data: Mapping[str, Any]) -> float:
    for name in SPEED_ATTRIBUTES:
        speed = _number(data.get(name))
        if speed is not None and speed > 0:
            return max(MIN_SPEED_KM_PER_H, speed)
    return DEFAULT_SPEED_KM_PER_H


def _cemt(data: Mapping[str, Any], skipped: Counter[str]) -> str | None:
    label = _text(data.get("cemt"))
    if not label:
        return None
    try:
        cemt = normalise_cemt_class(label)
    except ValidationError:
        skipped["cemt_unrecognised"] += 1
        return None
    return cemt if cemt_rank(cemt) > 0 else None


def build_euris_inputs(
    graph: nx.Graph,
    structures: EurisStructures | None = None,
    rules: EurisLimitRules | None = None,
    *,
    source: str = "euris-enriched",
) -> EurisInputs:
    """Build the network and per-arc limits from a loaded graph and structures.

    The arc id is ``<from node>|<to node>``, unique per edge. The section ``code`` links an
    edge to its locks and bridges. Several edges may share one code, and each gets the same
    structures. Fairway limits come from the edge itself: ``mwidthcm`` as beam and
    ``mdraughtcm`` as draught, both in cm.
    """
    rules = rules or EurisLimitRules()
    structures = structures or EurisStructures({}, {}, {}, {})
    skipped: Counter[str] = Counter()
    node_ids, coordinates = [], []
    for node, data in graph.nodes(data=True):
        point = data.get("geometry")
        if point is None or point.is_empty:
            raise ValidationError(f"Node {node!r} has no geometry")
        node_ids.append(str(node))
        coordinates.append((point.x, point.y))
    edges, lines, restrictions = [], [], []
    missing_length = 0
    for u, v, data in graph.edges(data=True):
        length_m = _number(data.get("length_m"))
        if length_m is None or length_m < 0:
            missing_length += 1
            continue
        arc_id = f"{u}|{v}"
        code = _text(data.get("code"))
        lock_ids = structures.section_locks.get(code, ()) if code else ()
        bridge_ids = structures.section_bridges.get(code, ()) if code else ()
        water_name = next(
            (_text(data.get(k)) for k in WATER_NAME_ATTRIBUTES if _text(data.get(k))), ""
        )
        edges.append(
            NetworkEdge(
                arc_id=arc_id,
                from_node=str(u),
                to_node=str(v),
                distance_km=length_m / 1000.0,
                speed_km_per_h=_speed(data),
                section_code=code,
                name=_text(data.get("name")),
                water_name=water_name,
                waterway_cemt_class=_cemt(data, skipped),
                lock_service_time_h=rules.lock_passage_time_h * len(lock_ids),
                lock_count=len(lock_ids),
            )
        )
        line = data.get("geometry")
        lines.append(line if line is not None and not getattr(line, "is_empty", True) else None)
        restrictions.extend(
            _arc_restrictions(arc_id, code, data, lock_ids, bridge_ids, structures, rules)
        )
    if missing_length:
        raise ValidationError(f"{missing_length} graph edges have no valid length_m")
    network = AssignmentNetwork(
        tuple(edges),
        tuple(node_ids),
        np.asarray(coordinates, dtype=np.float64),
        tuple(lines),
        source,
    )
    by_type = Counter(r.structure_type.value for r in restrictions)
    report = {
        "edges": len(edges),
        "nodes": len(node_ids),
        "edges_without_code": sum(1 for e in edges if not e.section_code),
        "edges_with_locks": sum(1 for e in edges if e.lock_count),
        "locks": len(structures.locks),
        "bridges": len(structures.bridges),
        "restrictions_by_type": dict(sorted(by_type.items())),
        "skipped": dict(sorted(skipped.items())),
        "rules": {
            "lock_passage_time_h": rules.lock_passage_time_h,
            "fairway_length": rules.fairway_length,
        },
    }
    return EurisInputs(network, tuple(restrictions), MappingProxyType(report))


def _arc_restrictions(
    arc_id: str,
    code: str,
    data: Mapping[str, Any],
    lock_ids: tuple[str, ...],
    bridge_ids: tuple[str, ...],
    structures: EurisStructures,
    rules: EurisLimitRules,
) -> list[ArcRestriction]:
    found = []
    beam = _positive(data.get("dim_structural_width")) or _positive(data.get("mwidthcm"), 0.01)
    draught = _positive(data.get("mdraughtcm"), 0.01)
    length = _positive(data.get("mlengthcm"), 0.01) if rules.fairway_length else None
    if beam or draught or length:
        found.append(
            ArcRestriction(
                arc_id,
                code or arc_id,
                StructureType.WATERWAY,
                "euris:fairway:mwidthcm,mdraughtcm" + (",mlengthcm" if length else ""),
                max_length_m=length,
                max_beam_m=beam,
                max_draught_m=draught,
            )
        )
    for lock_id in lock_ids:
        lock = structures.locks[lock_id]
        if lock.max_beam_m or lock.max_length_m or lock.max_draught_m:
            found.append(
                ArcRestriction(
                    arc_id,
                    lock_id,
                    StructureType.LOCK,
                    "euris:chambers:dim_gate_width,dim_usable_length,mdraughtcm",
                    max_length_m=lock.max_length_m,
                    max_beam_m=lock.max_beam_m,
                    max_draught_m=lock.max_draught_m,
                )
            )
    for bridge_id in bridge_ids:
        bridge = structures.bridges[bridge_id]
        if bridge.max_beam_m or bridge.max_air_draught_m:
            found.append(
                ArcRestriction(
                    arc_id,
                    bridge_id,
                    StructureType.BRIDGE,
                    "euris:openings:dim_gate_width,dim_height|cl_height|mheightcmc",
                    max_beam_m=bridge.max_beam_m,
                    max_air_draught_m=bridge.max_air_draught_m,
                )
            )
    return found


def load_euris_inputs(
    graph_path: str | Path,
    structures_dir: str | Path | None = None,
    rules: EurisLimitRules | None = None,
) -> EurisInputs:
    """Load the enriched graph and, when given, the detailed structures folder."""
    graph = load_euris_graph(graph_path)
    structures = load_euris_structures(structures_dir) if structures_dir is not None else None
    if structures is None:
        logger.warning("No EuRIS structures folder given: no lock or bridge limits or lock time")
    return build_euris_inputs(graph, structures, rules, source=Path(graph_path).name)
