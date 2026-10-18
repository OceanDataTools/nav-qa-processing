"""The nav33 reader: GGA dated by the logger's ISO timestamp (OpenRVDAS, Falkor (too))."""

from datetime import date

import pytest

from nav_qa_processing import formats
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


# -- nav02: NOAA SCS, the logger's MM/DD/YYYY,hh:mm:ss.sss (#81) --


def scs(stamp: str, hhmmss: str) -> str:
    gga = nmea(f"GPGGA,{hhmmss},4129.5473,N,07125.1176,W,2,10,1.5,15.9,M,,M,,")
    return f"{stamp},{gga}"


def test_scs_time():
    found = timestamped_gga.scs_time(
        "08/19/2025,12:37:31.377,$GPGGA,123730,4129.5473,N,07125.1176,W,2,10,1.5,15.9,M,,M,,*7A"
    )
    assert found == (date(2025, 8, 19), (12 * 3600 + 37 * 60 + 31.377) * 10**9)
    assert timestamped_gga.scs_time("13/19/2025,12:37:31.377,$GPGGA,") is None  # no month 13
    assert timestamped_gga.scs_time("2025-08-19T12:37:31Z,$GPGGA,") is None  # nav33's


def test_nav2_lines_are_dated_by_their_scs_timestamps():
    """EN735's logger runs a second or two after its Furuno's GGA times: a GGA for 23:59:59
    logged at 00:00:01 the next day is dated the day before
    """
    lines = [scs("08/19/2025,23:59:59.377", "235958"), scs("08/20/2025,00:00:01.020", "235959")]
    lines.append(scs("08/20/2025,00:00:02.010", "000000"))
    result = formats.get("nav02").reader.read([("GPS-Furuno-GGA_20250820-000000.Raw", lines)])
    assert [(f.day, f.gga.time.seconds) for f in result.fixes] == [
        (date(2025, 8, 19), 86398.0),
        (date(2025, 8, 19), 86399.0),
        (date(2025, 8, 20), 0.0),
    ]
    assert result.errors == []
    first, last = formats.get("nav02").reader.span(lines)
    assert (first.isoformat(), last.isoformat()) == (
        "2025-08-19T23:59:59.377000+00:00",
        "2025-08-20T00:00:02.010000+00:00",
    )


# -- nav15: OSU's DAS, DATA and an ISO timestamp, then the sentence, quoted or not --


def test_osu_time():
    found = timestamped_gga.osu_time('DATA, 2011-04-11T00:00:00.158Z, "$GPGGA,000000,…"')
    assert found == (date(2011, 4, 11), round(0.158e9))
    assert timestamped_gga.osu_time("2011-04-11T00:00:00.158Z,$GPGGA,…") is None  # nav33's


def test_nav15_quoted_and_unquoted_sentences_after_osu_headers():
    """Wecoma's files: META_/VESSEL/SOURCE header records, then DATA records; the sentence is in
    double quotes in the documented layout and bare in September 2011 (W1109B)
    """
    lines = [
        'META_VESSEL, "Name",       "Call_Sign", "IMO_Number"',
        'VESSEL,      "R/V Wecoma", "WSD7079",   "076044390"',
        'DATA, 2011-04-11T00:00:00.158Z, "$GPGGA,000000,4437.5473,N,12402.7120,W,2,11,0.8,8.5,M,'
        + '-21.8,M,,*70"',
        'DATA, 2011-04-11T00:00:00.286Z, "$GPGSA,A,3,,23,07,04,13,16,03,20,08,10,30,02,1.3,0.8,'
        + '1.1*36"',
        "DATA, 2011-09-14T23:59:59.569Z, "
        + nmea("GPGGA,000000.00,4438.48007,N,12429.40697,W,1,10,0.9,4.41,M,-26.15,M,,"),
    ]
    result = formats.get("nav15").reader.read([("gnss_adu5_starboardsuite.csv", lines)])
    assert [(f.day, f.gga.time.seconds) for f in result.fixes] == [
        (date(2011, 4, 11), 0.0),
        (date(2011, 9, 15), 0.0),  # logged 0.4 s before midnight: the next day's fix
    ]
    assert result.errors == [] and result.rejected_checksums == {}
