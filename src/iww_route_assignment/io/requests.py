"""Validated normalised request input and complete trip result tables."""

from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path

import pandas as pd

from ..application import CostedTrip
from ..domain.models import ArcCostRequest
from ..exceptions import ValidationError
from ._frames import (
    integer_value,
    number_value,
    optional_number_value,
    optional_text_value,
    read_csv_table,
    require_columns,
    text_value,
)
from .ivs import ARC_REQUEST_COLUMNS
from .results import arc_cost_to_record


def load_arc_requests_csv(path: str | Path) -> tuple[ArcCostRequest, ...]:
    """Require the version 0.2 complete-sequence schema; no inferred trip grouping."""
    table = read_csv_table(path)
    require_columns(table, set(ARC_REQUEST_COLUMNS), "arc requests (0.2 schema)")
    requests = []
    for _, row in table.iterrows():
        loaded_text = text_value(row, "is_loaded").strip().casefold()
        if loaded_text not in {"true", "false", "1", "0"}:
            raise ValidationError(f"Invalid is_loaded {loaded_text!r}")
        requests.append(
            ArcCostRequest(
                case_row_id=text_value(row, "case_row_id"),
                source_row_id=text_value(row, "source_row_id"),
                route_id=text_value(row, "route_id"),
                arc_id=text_value(row, "arc_id"),
                from_node_id=text_value(row, "from_node_id"),
                to_node_id=text_value(row, "to_node_id"),
                appearance=text_value(row, "appearance"),
                vessel_rws_class=text_value(row, "vessel_rws_class"),
                waterway_cemt_class=text_value(row, "waterway_cemt_class"),
                distance_km=number_value(row, "distance_km"),
                realised_speed_km_per_h=number_value(row, "realised_speed_km_per_h"),
                loading_ratio=number_value(row, "loading_ratio"),
                cargo_t=number_value(row, "cargo_t"),
                vessel_class_source=optional_text_value(row, "vessel_class_source", "supplied"),
                appearance_source=optional_text_value(row, "appearance_source", "supplied"),
                route_source=optional_text_value(row, "route_source", "supplied"),
                loading_ratio_source=optional_text_value(row, "loading_ratio_source", "supplied"),
                lock_service_time_h=optional_number_value(row, "lock_service_time_h"),
                lock_waiting_time_h=optional_number_value(row, "lock_waiting_time_h"),
                bridge_service_time_h=optional_number_value(row, "bridge_service_time_h"),
                real_delay_time_h=optional_number_value(row, "real_delay_time_h"),
                calibration_delay_time_h=optional_number_value(row, "calibration_delay_time_h"),
                relaxation_penalty_time_h=optional_number_value(row, "relaxation_penalty_time_h"),
                sequence=integer_value(row, "sequence"),
                route_arc_count=integer_value(row, "route_arc_count"),
                is_loaded=loaded_text in {"true", "1"},
                cargo_unit_source=optional_text_value(row, "cargo_unit_source", "supplied:tonnes"),
                notes=tuple(filter(None, optional_text_value(row, "notes").split("; "))),
            )
        )
    return tuple(requests)


def cost_results_to_frames(trips: Iterable[CostedTrip]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return per-traversal components and per-trip totals without repeating fixed cost."""
    arc_rows = []
    trip_rows = []
    for trip in trips:
        for arc in trip.arcs:
            request = {
                field.name: getattr(arc.request, field.name) for field in fields(arc.request)
            }
            request["notes"] = "; ".join(arc.request.notes)
            arc_rows.append(
                {
                    **request,
                    **arc_cost_to_record(arc.request.arc_id, arc.cost),
                    "cost_rate_source": trip.cost_rate_source,
                }
            )
        trip_rows.append(
            {
                "source_row_id": trip.source_row_id,
                "route_id": trip.route_id,
                "arc_count": len(trip.arcs),
                "cost_rate_source": trip.cost_rate_source,
                **asdict(trip.fixed),
                **asdict(trip.cost),
                "total_cost_eur": trip.cost.total_cost_eur,
                "route_sources": "; ".join(sorted({arc.request.route_source for arc in trip.arcs})),
                "cargo_unit_source": trip.arcs[0].request.cargo_unit_source,
            }
        )
    return pd.DataFrame.from_records(arc_rows), pd.DataFrame.from_records(trip_rows)
