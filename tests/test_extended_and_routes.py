"""Dual-source and complete-trip regressions using licence-safe synthetic inputs."""

from dataclasses import replace

import pandas as pd
import pytest

from iww_route_assignment import (
    ArcProfile,
    RouteSequence,
    TripRoute,
    ValidationError,
    build_route_index,
    cost_trip_requests,
    summarise_trip_conversion,
    trips_to_arc_requests,
)
from iww_route_assignment.io import (
    arc_requests_to_frame,
    cost_results_to_frames,
    load_arc_profiles_csv,
    load_arc_requests_csv,
    load_route_sequences_csv,
    load_trip_routes_csv,
    read_ivs_frame,
)


def extended(**values):
    row = dict(
        Jaar="2026",
        Pseudo_ReisID="0001",
        SK_CODE="M1",
        v28_Beladingscode="7",
        Segment="Droge bulkvaart",
        Lading="100",
        Aantal_Schepen="1",
        Laadvermogen="200",
        UNLO_Herkomst="NLAAA",
        UNLO_Bestemming="NLBBB",
        Begindtijd_evenement="01Jan2026:00:44:00",
        Begintijd_evenement="2026-01-01 00:44:00",
    )
    row.update(values)
    return pd.DataFrame([row])


def parse(frame, **kwargs):
    return read_ivs_frame(
        frame, source_dataset="test", format="extended_2026", cargo_weight_unit="t", **kwargs
    )


def route():
    return RouteSequence(
        "r",
        "NLAAA",
        "NLBBB",
        (
            ArcProfile("ab", "a", "b", 10, "I", 10, "synthetic"),
            ArcProfile("bc", "b", "c", 20, "I", 10, "synthetic", calibration_delay_time_h=0.5),
        ),
        "synthetic:not-an-observed-route",
    )


def requests(empty=False):
    trip = parse(
        extended(Lading="0" if empty else "100", v28_Beladingscode="1" if empty else "7")
    ).trips[0]
    return trips_to_arc_requests([trip], routes=build_route_index([route()])).requests


def test_extended_identity_units_segment_and_unknown_teu():
    trip = parse(extended()).trips[0]
    assert trip.source_row_id == "test:2026:0001"
    assert trip.vessel_rws_class == "M1"
    assert trip.vessel_type_id is None
    assert trip.containers_teu is None
    assert trip.cargo_t == 100
    assert trip.appearance == "droge_bulk"
    assert "assumption" in trip.cargo_unit_source
    assert trip.observed_on == "2026-01-01T00:44:00"
    assert parse(extended(Segment="Tankvaart")).trips[0].appearance == "natte_bulk"


def test_units_are_declared_and_kg_converts():
    with pytest.raises(ValidationError, match="cargo_weight_unit"):
        read_ivs_frame(extended(), source_dataset="test", format="extended_2026")
    result = read_ivs_frame(
        extended(Lading="100000"),
        source_dataset="test",
        format="extended_2026",
        cargo_weight_unit="kg",
    )
    assert result.trips[0].cargo_t == 100


@pytest.mark.parametrize(
    "change",
    [
        {"Lading": "-1"},
        {"Lading": "inf"},
        {"Laadvermogen": "bad"},
        {"Aantal_Schepen": "2"},
        {"Aantal_Schepen": "inf"},
        {"Aantal_Schepen": "1.5"},
        {"Segment": "unknown"},
        {"SK_CODE": ""},
        {"Pseudo_ReisID": ""},
        {"v28_Beladingscode": "1"},
        {"Begintijd_evenement": "2026-01-02 00:44:00"},
        {"Begintijd_evenement": "bad date"},
    ],
)
def test_bad_extended_rows_are_audited(change):
    result = parse(extended(**change))
    assert not result.trips
    assert len(result.rejections) == 1


def test_missing_weight_is_not_silently_empty():
    assert parse(extended(Lading=None)).trips[0].cargo_t is None
    assert parse(extended(Lading=None, v28_Beladingscode="1")).trips[0].cargo_t == 0


def test_raw_export_index_needs_explicit_permission():
    frame = extended()
    frame.insert(0, "Unnamed: 0", [0])
    with pytest.raises(ValidationError, match="index"):
        parse(frame)
    assert "dropped_export_index" in parse(frame, drop_export_index=True).trips[0].notes[-1]


def test_duplicate_source_ids_fail_even_if_one_row_is_bad():
    with pytest.raises(ValidationError, match="Duplicate"):
        parse(pd.concat([extended(Lading="bad"), extended()], ignore_index=True))


def test_validation_columns_do_not_affect_cost_inputs():
    a = parse(extended()).trips[0]
    b = parse(extended(Reisafstand="9999", Vaarsnelheid="9999", Eerste_Telpunt="x")).trips[0]
    assert a == b


def test_weekly_observed_class_is_not_replaced_by_numeric_bivas_id():
    frame = pd.DataFrame(
        [
            dict(
                unlo_herkomst="NLAAA",
                unlo_bestemming="NLBBB",
                sk_code="M1",
                v15_1_scheepstype_rws=99,
                v18_laadvermogen=200,
                v38_vervoerd_gewicht=100000,
                v30_4_containers_teu_s=None,
                nstr_nw=2,
            )
        ]
    )
    trip = read_ivs_frame(frame, source_dataset="weekly").trips[0]
    assert trip.vessel_rws_class == "M1"
    assert trip.cargo_t == parse(extended()).trips[0].cargo_t
    converted = trips_to_arc_requests(
        [trip], routes=build_route_index([route()]), vessel_types={99: "M12"}
    )
    assert converted.requests[0].vessel_rws_class == "M1"


def test_two_arcs_count_as_one_trip():
    trip = parse(extended()).trips[0]
    result = trips_to_arc_requests([trip], routes=build_route_index([route()]))
    summary = summarise_trip_conversion(result)
    assert summary["accepted_rows"] == 1
    assert summary["arc_requests"] == 2
    assert [r.sequence for r in result.requests] == [0, 1]
    assert all(r.route_arc_count == 2 for r in result.requests)


@pytest.mark.parametrize(
    "empty,reported,total,fixed", [(True, 420, 435, 0), (False, 1054, 1069, 514)]
)
def test_complete_trip_arithmetic_and_csv_roundtrip(
    tmp_path, cost_rates, scenario, fuel_provider, empty, reported, total, fixed
):
    rates = replace(cost_rates, appearance="droge_bulk")
    rows = requests(empty)
    path = tmp_path / "requests.csv"
    arc_requests_to_frame(rows).to_csv(path, index=False)
    loaded = load_arc_requests_csv(path)
    assert loaded == rows
    results = cost_trip_requests(reversed(loaded), [rates], scenario, fuel_provider)
    assert results[0].cost.reported_total_cost_eur == reported
    assert results[0].cost.total_cost_eur == total
    assert results[0].cost.fixed_cost_eur == fixed
    arcs, trips = cost_results_to_frames(results)
    assert "fixed_cost_eur" not in arcs
    assert trips["total_cost_eur"].tolist() == [total]
    assert trips["choice_total_cost_eur"].tolist() == [total]


@pytest.mark.parametrize("kind", ["missing", "duplicate", "disconnected", "cargo", "route"])
def test_incomplete_or_inconsistent_trip_never_produces_total(
    cost_rates, scenario, fuel_provider, kind
):
    a, b = requests()
    variants = {
        "missing": [a],
        "duplicate": [a, a],
        "disconnected": [a, replace(b, from_node_id="elsewhere")],
        "cargo": [a, replace(b, cargo_t=101)],
        "route": [a, replace(b, route_id="other")],
    }
    with pytest.raises(ValidationError):
        cost_trip_requests(
            variants[kind], [replace(cost_rates, appearance="droge_bulk")], scenario, fuel_provider
        )


def test_explicit_associations_and_ambiguous_od():
    trip = parse(extended()).trips[0]
    routes = build_route_index([route(), replace(route(), route_id="alternative")])
    assert not trips_to_arc_requests([trip], routes=routes).requests
    result = trips_to_arc_requests(
        [trip], routes=routes, trip_routes=[TripRoute(trip.source_row_id, "r")]
    )
    assert len(result.requests) == 2
    with pytest.raises(ValidationError, match="absent"):
        trips_to_arc_requests([], routes=routes, trip_routes=[TripRoute("missing", "r")])
    with pytest.raises(ValidationError, match="Duplicate"):
        trips_to_arc_requests(
            [trip], routes=routes, trip_routes=[TripRoute("x", "r"), TripRoute("x", "r")]
        )
    with pytest.raises(ValidationError, match="Unknown route"):
        trips_to_arc_requests([trip], routes=routes, trip_routes=[TripRoute("x", "missing")])


def test_disconnected_and_empty_routes_are_rejected():
    with pytest.raises(ValidationError, match="Disconnected"):
        replace(route(), arcs=tuple(reversed(route().arcs)))
    with pytest.raises(ValidationError, match="no arcs"):
        replace(route(), arcs=())


def test_route_loader_preserves_repeated_edges_and_identifiers(tmp_path):
    arcs = tmp_path / "arcs.csv"
    arcs.write_text(
        "arc_id,from_node_id,to_node_id,distance_km,waterway_cemt_class,realised_speed_km_per_h,source\n001,01,02,1,I,10,test\n002,02,01,1,I,10,test\n"
    )
    sequence = tmp_path / "sequence.csv"
    sequence.write_text(
        "route_id,origin_locode,destination_locode,sequence,arc_id,source\nr,NLAAA,NLBBB,2,001,test\nr,NLAAA,NLBBB,0,001,test\nr,NLAAA,NLBBB,1,002,test\n"
    )
    loaded = load_route_sequences_csv(sequence, load_arc_profiles_csv(arcs))[0]
    assert [a.arc_id for a in loaded.arcs] == ["001", "002", "001"]
    assert loaded.arcs[0].from_node_id == "01"
    associations = tmp_path / "trips.csv"
    associations.write_text("source_row_id,route_id\n0001,r\n")
    assert load_trip_routes_csv(associations) == (TripRoute("0001", "r"),)


@pytest.mark.parametrize(
    "rows,match",
    [
        ("r,NLAAA,NLBBB,1,ab,test\n", "contiguous"),
        ("r,NLAAA,NLBBB,0,missing,test\n", "Unknown arc"),
        ("r,NLAAA,NLBBB,0,ab,test\nr,NLAAA,NLBBB,0,bc,test\n", "contiguous"),
        ("r,NLAAA,NLBBB,0,ab,test\nr,NLAAA,NLCCC,1,bc,test\n", "Inconsistent"),
    ],
)
def test_bad_sequences_fail(tmp_path, rows, match):
    path = tmp_path / "routes.csv"
    path.write_text("route_id,origin_locode,destination_locode,sequence,arc_id,source\n" + rows)
    with pytest.raises(ValidationError, match=match):
        load_route_sequences_csv(path, route().arcs)
