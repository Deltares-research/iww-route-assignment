"""CSV adapters for supplied directed edges and ordered routes, not route search."""

from collections.abc import Iterable
from pathlib import Path

from ..domain.arcs import TIME_FIELDS, ArcProfile, RouteSequence, TripRoute
from ..exceptions import ValidationError
from ._frames import (
    integer_value,
    number_value,
    optional_number_value,
    read_csv_table,
    require_columns,
    text_value,
)


def load_arc_profiles_csv(path: str | Path) -> tuple[ArcProfile, ...]:
    """Read an explicit edge snapshot; zero-distance structures are allowed."""
    table = read_csv_table(path)
    require_columns(
        table,
        {
            "arc_id",
            "from_node_id",
            "to_node_id",
            "distance_km",
            "waterway_cemt_class",
            "realised_speed_km_per_h",
            "source",
        },
        "arc profiles",
    )
    result = tuple(
        ArcProfile(
            text_value(row, "arc_id"),
            text_value(row, "from_node_id"),
            text_value(row, "to_node_id"),
            number_value(row, "distance_km"),
            text_value(row, "waterway_cemt_class"),
            number_value(row, "realised_speed_km_per_h"),
            text_value(row, "source"),
            **{name: optional_number_value(row, name) for name in TIME_FIELDS},
        )
        for _, row in table.iterrows()
    )
    if len({arc.arc_id for arc in result}) != len(result):
        raise ValidationError("Duplicate arc_id in arc profiles")
    return result


def load_route_sequences_csv(
    path: str | Path, arcs: Iterable[ArcProfile]
) -> tuple[RouteSequence, ...]:
    """Read zero-based ordered traversals; reject gaps, missing arcs and discontinuity."""
    arc_rows = tuple(arcs)
    index = {arc.arc_id: arc for arc in arc_rows}
    if len(index) != len(arc_rows):
        raise ValidationError("Duplicate arc_id in arc profiles")
    table = read_csv_table(path)
    require_columns(
        table,
        {"route_id", "origin_locode", "destination_locode", "sequence", "arc_id", "source"},
        "route sequences",
    )
    result = []
    for route_id, group in table.groupby("route_id", sort=True, dropna=False):
        ordered = sorted(
            ((integer_value(row, "sequence"), row) for _, row in group.iterrows()),
            key=lambda item: item[0],
        )
        if [sequence for sequence, _ in ordered] != list(range(len(group))):
            raise ValidationError(
                f"Route {route_id!r} needs contiguous unique sequence numbers from zero"
            )
        for column in ("origin_locode", "destination_locode", "source"):
            if group[column].nunique(dropna=False) != 1:
                raise ValidationError(f"Inconsistent {column} for route {route_id!r}")
        try:
            members = tuple(index[text_value(row, "arc_id")] for _, row in ordered)
        except KeyError as error:
            raise ValidationError(
                f"Unknown arc_id {error.args[0]!r} in route {route_id!r}"
            ) from error
        first = ordered[0][1]
        result.append(
            RouteSequence(
                text_value(first, "route_id"),
                text_value(first, "origin_locode"),
                text_value(first, "destination_locode"),
                members,
                text_value(first, "source"),
            )
        )
    return tuple(result)


def load_trip_routes_csv(path: str | Path) -> tuple[TripRoute, ...]:
    """Read explicit trip-to-route associations; do not pick among duplicates."""
    table = read_csv_table(path)
    require_columns(table, {"source_row_id", "route_id"}, "trip routes")
    result = tuple(
        TripRoute(text_value(row, "source_row_id"), text_value(row, "route_id"))
        for _, row in table.iterrows()
    )
    if len({row.source_row_id for row in result}) != len(result):
        raise ValidationError("Duplicate source_row_id in trip routes")
    return result
