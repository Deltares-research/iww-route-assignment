"""Stable flat records and validation summaries for result adapters."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import fields
from enum import Enum
from typing import Any

from ..domain.enums import Confidence
from ..domain.models import ArcCostBreakdown, ResolvedFuelRate


def arc_cost_to_record(arc_id: str, result: ArcCostBreakdown) -> dict[str, Any]:
    """Flatten one arc result for CSV, Parquet or JSON output."""
    record: dict[str, Any] = {"arc_id": arc_id}
    for field in fields(result):
        if field.name != "fuel":
            record[field.name] = getattr(result, field.name)
    record.update(fuel_resolution_to_record(result.fuel, prefix="fuel_"))
    return record


def fuel_resolution_to_record(result: ResolvedFuelRate, *, prefix: str = "") -> dict[str, Any]:
    """Flatten fuel audit metadata without losing proxy terms or notes."""
    record: dict[str, Any] = {}
    for field in fields(result):
        value = getattr(result, field.name)
        if field.name == "proxy_distance_terms":
            value = json.dumps(dict(value), sort_keys=True)
        elif field.name in {"source_row_ids", "source_datasets", "notes"}:
            value = json.dumps(value)
        elif isinstance(value, Enum):
            value = value.value
        record[f"{prefix}{field.name}"] = value
    return record


def summarise_fuel_resolutions(results: Iterable[ResolvedFuelRate]) -> dict[str, Any]:
    """Count fuel methods and confidence, including the low-confidence share."""
    rows = list(results)
    method_counts = Counter(result.method.value for result in rows)
    confidence_counts = Counter(result.confidence.value for result in rows)
    low_count = sum(result.confidence in {Confidence.LOW, Confidence.FALLBACK} for result in rows)
    return {
        "total": len(rows),
        "method_counts": dict(sorted(method_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "low_or_fallback_count": low_count,
        "low_or_fallback_share": low_count / len(rows) if rows else 0.0,
    }
