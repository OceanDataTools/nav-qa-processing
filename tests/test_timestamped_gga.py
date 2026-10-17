"""The nav33 reader: GGA dated by the logger's ISO timestamp (OpenRVDAS, Falkor (too))."""

from datetime import date

import pytest

from nav_qa_processing.formats import timestamped_gga
from nav_qa_processing.formats.nmea import checksum


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}"


def line(stamp: str, hhmmss: str, separator: str = ",") -> str:
    gga = nmea(f"GPGGA,{hhmmss},2635.03241,N,09331.21859,W,2,09,1.1,1.89,M,,,9,0025")
    return f"{stamp}{separator}{gga}"


def dated(lines: list[str]) -> list[tuple[date, float]]:
    result = timestamped_gga.read([("f", lines)])
    return [(f.day, f.gga.time.seconds) for f in result.fixes]


def test_openvdm_samples_line():
    sample = (
        "2012-04-28T00:00:01.109000Z,$GPGGA,000000.987,2635.03241,N,09331.21859,W,2,09,1.1,"
        "1.89,M,,,9,0025*16"
    )
    result = timestamped_gga.read([("POSMV_GGA-2012-04-28-000000.txt", [sample])])
    (fix,) = result.fixes
    assert fix.day == date(2012, 4, 28) and fix.gga.time.seconds == 0.987
    assert (fix.gga.lon, fix.gga.lat) == pytest.approx((-(93 + 31.21859 / 60), 26 + 35.03241 / 60))
    assert result.errors == [] and result.rejected_checksums == {}


def test_the_date_is_the_one_nearest_the_logger_timestamp():
    lines = [
        line("2026-07-09T23:59:59.900000Z", "235959.80"),
        line("2026-07-10T00:00:00.100000Z", "235959.95"),  # logged after midnight, fixed before
        line("2026-07-09T23:59:59.990000Z", "000000.05"),  # logged before midnight, fixed after
        line("2026-07-10T00:00:00.200000Z", "000000.10"),
    ]
    assert dated(lines) == [
        (date(2026, 7, 9), 86399.8),
        (date(2026, 7, 9), 86399.95),
        (date(2026, 7, 10), 0.05),
        (date(2026, 7, 10), 0.1),
    ]


def test_a_space_separates_the_timestamp_too():
    assert dated([line("2026-07-09T12:00:00.5Z", "120000.40", " ")]) == [
        (date(2026, 7, 9), 43200.4)
    ]


def test_a_header_line_is_ignored():
    header = "Timestamp,Header,GPSTime,Latitude,NorS,Longitude,EorW,FixQuality,NumSats,HDOP"
    result = timestamped_gga.read([("f", [header, line("2026-07-09T12:00:00Z", "120000.00")])])
    assert len(result.fixes) == 1 and result.errors == []


def test_problems_are_recorded_not_raised():
    bad_checksum = line("2026-07-09T12:00:01Z", "120001.00")[:-2] + "00"
    no_stamp = line("", "120002.00").lstrip(",")
    timeless = "2026-07-09T12:00:03Z," + nmea("GPGGA,,2635.0,N,09331.2,W,0,00,,,M,,,,")
    result = timestamped_gga.read(
        [("f", [line("2026-07-09T12:00:00Z", "120000.00"), bad_checksum, no_stamp, timeless])]
    )
    assert len(result.fixes) == 1
    assert result.rejected_checksums == {"f": 1}
    assert result.errors == [
        "f: 1 GGA without a time, skipped",
        "f: 1 GGA without a logger timestamp, skipped",
    ]
    # Counted by reason too (#67)
    assert result.skipped == {
        "f": {
            "GGA without a time": 1,
            "GGA without a logger timestamp": 1,
            "failed its checksum": 1,
        }
    }


def test_repeated_times_are_kept_for_qc():
    lines = [
        line("2026-07-09T12:00:00.1Z", "120000.00"),
        line("2026-07-09T12:00:00.6Z", "120000.00"),
    ]
    assert len(dated(lines)) == 2


def test_a_files_span_is_its_first_and_last_timestamp():
    lines = [
        "Timestamp,Header",
        line("2026-07-09T12:00:00Z", "120000.00"),
        line("2026-07-10T01:00:00.5Z", "010000.40"),
    ]
    first, last = timestamped_gga.span(lines)
    assert (first.isoformat(), last.isoformat()) == (
        "2026-07-09T12:00:00+00:00",
        "2026-07-10T01:00:00.500000+00:00",
    )
    assert timestamped_gga.span(["no timestamps here"]) is None
