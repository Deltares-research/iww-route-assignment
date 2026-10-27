"""Canonical class labels and ordinal CEMT comparisons."""

from __future__ import annotations

import json
import math
import pathlib
import re

from ..exceptions import ValidationError

CEMT_ORDER: tuple[str, ...] = (
    "0",
    "I",
    "II",
    "III",
    "IV",
    "Va",
    "Vb",
    "VIa",
    "VIb",
    "VIc",
    "VII",
)

_RWS_ALIASES = {
    "BII-6B": "BII-6b",
}

# Convoy labels end in a length/beam letter written in either case by different sources,
# for example BII-2b (IVS) and BII-2B (DTV). Only the letter case is canonicalised here;
# different codes such as B04 and BO4 are never treated as the same class.
_CONVOY_SUFFIX = re.compile(r"^(BII-\d|C\d)([LB])$")

_CEMT_ALIASES = {
    "IVA": "IV",
    "VA": "Va",
    "VB": "Vb",
    "VIA": "VIa",
    "VIB": "VIb",
    "VIC": "VIc",
}


def is_valid(val):
    if val is None:
        return False
    if isinstance(val, float) and math.isnan(val):
        return False
    if str(val) == "nan":
        return False
    return True


def normalize_class_code(code: str) -> str:
    """Normalizes the IVS ship class code to match the DTV class naming convention."""
    if not isinstance(code, str):
        return ""
    code = code.upper().strip()
    if code.startswith("B0"):
        code = "BO" + code[2:]
    return code


def normalise_rws_class(value: str) -> str:
    """Return a canonical RWS vessel-class label."""
    label = value.strip()
    if not label:
        raise ValidationError("vessel_rws_class must not be empty")
    upper = label.upper()
    if upper in _RWS_ALIASES:
        return _RWS_ALIASES[upper]
    if upper.startswith("M") and upper[1:].isdigit():
        return upper
    convoy = _CONVOY_SUFFIX.match(upper)
    if convoy:
        return f"{convoy.group(1)}{convoy.group(2).lower()}"
    return label


def normalise_cemt_class(value: str) -> str:
    """Return a canonical supported waterway CEMT class."""
    label = value.strip()
    canonical = _CEMT_ALIASES.get(label.upper(), label)
    if canonical not in CEMT_ORDER:
        expected = ", ".join(CEMT_ORDER)
        raise ValidationError(
            f"Unsupported waterway CEMT class {value!r}; expected one of: {expected}"
        )
    return canonical


def cemt_rank(value: str) -> int:
    """Return the ordinal rank used for interpolation and nearest-class selection."""
    return CEMT_ORDER.index(normalise_cemt_class(value))


def vessel_family(vessel_rws_class: str) -> str:
    """Return a coarse RWS configuration family for proxy selection."""
    canonical = normalise_rws_class(vessel_rws_class)
    if canonical.startswith("M"):
        return "motor_vessel"
    if canonical.startswith("B"):
        return "push_convoy"
    if canonical.startswith("C"):
        return "coupled_convoy"
    return "other"


def get_ship_dimensions(
    sk_code: str, cargo_weight: float, capacity: float, dtv_db: dict
) -> dict:
    """Computes dynamic vessel dimensions (including loaded/empty draft interpolation)."""
    normalized = normalize_class_code(sk_code)
    ship = dtv_db.get(normalized)

    if not ship:
        return {
            "beam": 11.4,
            "length": 110.0,
            "height": 6.0,
            "draft": 2.5,
            "cemt": "VB",
        }

    beam = float(ship.get("Beam [m]", 11.4))
    length = float(ship.get("Length [m]", 110.0))

    h_avg = ship.get("Height average [m]")
    height = float(h_avg) if is_valid(h_avg) else 6.0

    d_loaded = ship.get("Draught loaded [m]")
    d_empty = ship.get("Draught empty [m]")
    d_avg = ship.get("Draught average [m]")

    loaded_draft = (
        float(d_loaded)
        if is_valid(d_loaded)
        else (float(d_avg) if is_valid(d_avg) else 3.5)
    )
    empty_draft = float(d_empty) if is_valid(d_empty) else (0.4 * loaded_draft)

    if capacity > 0 and cargo_weight > 0:
        ratio = cargo_weight / capacity
        ratio = max(0.0, min(1.0, ratio))
        draft = empty_draft + ratio * (loaded_draft - empty_draft)
    else:
        draft = empty_draft

    ship_cemt = normalise_cemt_class(ship.get("CEMT-class", ""))

    return {
        "beam": beam,
        "length": length,
        "height": height,
        "draft": draft,
        "cemt": ship_cemt,
    }


def load_shiptypes(reference_dir: pathlib.Path) -> dict:
    """Loads and indexes the DTV ship types database."""
    dtv_path = reference_dir / "DTV_shiptypes_database.json"
    if not dtv_path.exists():
        raise FileNotFoundError(
            f"DTV shiptypes database not found at {dtv_path}. Run 'make download-reference' first."
        )

    with open(dtv_path, "r", encoding="utf-8") as f:
        ships = json.load(f)

    indexed = {}
    for ship in ships:
        rws_class = ship.get("RWS-class")
        if rws_class:
            indexed[rws_class.upper().strip()] = ship

    return indexed
