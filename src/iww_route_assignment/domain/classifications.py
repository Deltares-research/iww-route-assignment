"""Canonical class labels and ordinal CEMT comparisons."""

from __future__ import annotations

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
