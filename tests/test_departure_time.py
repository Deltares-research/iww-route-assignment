"""Departure time on trips: which column is read, and what happens to bad values."""

from datetime import datetime

import pandas as pd

from iww_route_assignment.io import read_ivs_frame


def _extended(**values: str) -> pd.DataFrame:
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
    )
    row.update(values)
    return pd.DataFrame([row])


def _read_extended(frame: pd.DataFrame, **kwargs: str):
    return read_ivs_frame(
        frame, source_dataset="t", format="extended_2026", cargo_weight_unit="t", **kwargs
    )


def _weekly(**values: str) -> pd.DataFrame:
    row = dict(
        unlo_herkomst="NLAAA",
        unlo_bestemming="NLBBB",
        v15_1_scheepstype_rws="2",
        sk_code="M1",
        v18_laadvermogen="350",
        v28_beladingscode="7",
        v38_vervoerd_gewicht="175000",
        v30_4_containers_teu_s="0",
        nstr_nw="2",
        v05_06_begindt_evenement_iso="2023-01-02T06:30:00",
    )
    row.update(values)
    return pd.DataFrame([row])


def test_extended_trip_reads_first_counting_point_time() -> None:
    trip = _read_extended(_extended(Eerste_Tijdstip="02Jan2026:13:45:10")).trips[0]
    assert trip.departure_time == datetime(2026, 1, 2, 13, 45, 10)
    assert trip.observed_on == "2026-01-01T00:44:00"
    assert not any(note.startswith("departure_time") for note in trip.notes)


def test_extended_departure_is_not_the_event_time() -> None:
    assert _read_extended(_extended()).trips[0].departure_time is None


def test_iso_text_and_custom_column() -> None:
    frame = _extended(Eerste_Tijdstip="", Aankomst="2026-03-04 05:06:07")
    assert _read_extended(frame).trips[0].departure_time is None
    trip = _read_extended(frame, departure_column="Aankomst").trips[0]
    assert trip.departure_time == datetime(2026, 3, 4, 5, 6, 7)


def test_weekmonitor_reads_event_time_at_origin() -> None:
    trip = read_ivs_frame(_weekly(), source_dataset="t", format="weekmonitor").trips[0]
    assert trip.departure_time == datetime(2023, 1, 2, 6, 30)


def test_date_only_time_is_midnight_and_noted() -> None:
    frame = _weekly(v05_06_begindt_evenement_iso="2023-01-02")
    trip = read_ivs_frame(frame, source_dataset="t", format="weekmonitor").trips[0]
    assert trip.departure_time == datetime(2023, 1, 2)
    assert "departure_time_date_only:v05_06_begindt_evenement_iso" in trip.notes


def test_unreadable_time_does_not_reject_the_trip() -> None:
    frame = _extended(Eerste_Tijdstip="not a time")
    result = _read_extended(frame)
    assert not result.rejections
    assert result.trips[0].departure_time is None
    assert "departure_time_unreadable:Eerste_Tijdstip" in result.trips[0].notes
