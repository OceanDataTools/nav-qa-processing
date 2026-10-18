from datetime import date
from itertools import pairwise

import pytest

from nav_qa_processing import formats
from nav_qa_processing.formats import gga_zda
from nav_qa_processing.formats.nmea import checksum


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}"


def gga(hhmmss: str) -> str:
    return nmea(f"GPGGA,{hhmmss},3242.0000,N,11714.0000,W,2,12,0.8,7.360,M,-35.6,M,,")


def zda(hhmmss: str, day: int, month: int = 7, year: int = 2026) -> str:
    return nmea(f"GPZDA,{hhmmss},{day:02d},{month:02d},{year},00,00")


def dated(lines: list[str]) -> list[tuple[date, str]]:
    result = gga_zda.read([("f", lines)])
    return [(f.day, f"{f.gga.time.ns // 10**9:05d}") for f in result.fixes]


def test_zda_dates_preceding_ggas():
    lines = [gga("120000.00"), zda("120000.01", 9), gga("120001.00"), zda("120001.01", 9)]
    assert dated(lines) == [(date(2026, 7, 9), "43200"), (date(2026, 7, 9), "43201")]


def test_buffer_before_late_zda_spans_midnight():
    lines = [gga("235959.00"), gga("000000.50"), zda("000001.00", 10)]
    assert [d for d, _ in dated(lines)] == [date(2026, 7, 9), date(2026, 7, 10)]


def test_day_advances_when_time_decreases_after_zda_run():
    lines = [gga("235958.00"), gga("235959.00"), gga("000001.00"), zda("235958.50", 9)]
    assert [d for d, _ in dated(lines)] == [date(2026, 7, 9), date(2026, 7, 9), date(2026, 7, 10)]


def test_repeated_time_is_kept_on_the_same_day():
    """NavManager drops a repeat of the last dated time, or moves it to the next day (#14, #15);
    here it's kept, dated, and left for QC to flag
    """
    lines = [gga("120000.00"), zda("120000.01", 9), gga("120000.00"), gga("120001.00")]
    lines.append(zda("120001.01", 9))
    assert dated(lines) == [(date(2026, 7, 9), "43200")] * 2 + [(date(2026, 7, 9), "43201")]


def test_gga_after_the_last_zda_is_kept():
    """NavManager's final loop drops the last of these (#14)"""
    lines = [zda("120000.00", 9), gga("120001.00"), gga("120002.00"), gga("120003.00")]
    assert [t for _, t in dated(lines)] == ["43201", "43202", "43203"]


def test_gga_at_midnight_before_anything_is_dated_is_kept():
    lines = [gga("000000.00"), zda("000000.01", 10)]
    assert dated(lines) == [(date(2026, 7, 10), "00000")]


def test_nearest_zda_dates_each_gga_across_midnight():
    lines = [zda("235959.50", 9), gga("235959.80"), gga("000000.20"), zda("000000.50", 10)]
    assert [d for d, _ in dated(lines)] == [date(2026, 7, 9), date(2026, 7, 10)]


def test_ggas_before_the_first_zda_take_its_date():
    lines = [gga("235959.00"), gga("235959.50"), zda("000000.01", 10), gga("000000.50")]
    assert [d for d, _ in dated(lines)] == [date(2026, 7, 9), date(2026, 7, 9), date(2026, 7, 10)]


def test_buffer_carries_across_files():
    result = gga_zda.read([("a", [gga("120000.00")]), ("b", [zda("120000.01", 9)])])
    assert [(f.source_file, f.day) for f in result.fixes] == [("a", date(2026, 7, 9))]


def test_no_zda_is_an_error_not_an_exception():
    result = gga_zda.read([("f", [gga("120000.00")])])
    assert result.fixes == [] and "no ZDA" in result.errors[0]
    assert result.skipped == {"f": {gga_zda.NO_ZDA_TO_DATE_IT: 1}}


def test_invalid_zda_is_recorded_and_skipped():
    result = gga_zda.read([("f", [gga("120000.00"), nmea("GPZDA,120000.01,,,,,")])])
    assert "ZDA without a valid date" in result.errors[0]


def test_skipped_sentences_are_counted_per_file_and_reason():
    timeless = nmea("GPGGA,,,,,,0,00,,,M,,M,,")
    bad = gga("120002.00")[:-2] + "00"
    result = gga_zda.read(
        [
            ("a", [gga("120000.00"), timeless, timeless, zda("120000.01", 9)]),
            ("b", [gga("120001.00"), bad, nmea("GPZDA,120001.01,,,,,"), zda("120001.02", 9)]),
        ]
    )
    assert len(result.fixes) == 2
    assert result.skipped == {
        "a": {gga_zda.NO_TIME: 2},
        "b": {gga_zda.NO_VALID_DATE: 1, gga_zda.FAILED_CHECKSUM: 1},
    }


def test_a_file_starting_after_a_long_gap_takes_its_own_zda():
    """The first GGA of a file that starts 13.5 h after the last one ended sits next to the
    previous file's last ZDA, but belongs with the ZDA right after it (SP2503)
    """
    first = [gga("023307.50"), zda("023308.01", 13)]
    second = [gga("160333.50"), zda("160334.01", 13), gga("160334.00")]
    result = gga_zda.read([("a", first), ("b", second)])
    assert [f.day for f in result.fixes] == [date(2026, 7, 13)] * 3


def logged(sentence: str, *cuts: int, stamp: str = "2024-06-02T00:00:00.105640Z ") -> list[str]:
    """`sentence` as a logger splits it: in pieces at `cuts`, each on a timestamped line"""
    bounds = [0, *cuts, len(sentence)]
    return [stamp + sentence[a:b] for a, b in pairwise(bounds)]


def test_split_sentences_are_joined():
    """RR2406's logger: sentences split anywhere, inside the checksum too"""
    fix = nmea("GPGGA,000000.00,2558.9102,N,08312.6475,W,1,08,0.8,11.8,M,-24.4,M,,")
    lines = [*logged(zda("000000.00", 2, 6, 2024), 12), *logged(fix, 70, len(fix) - 1)]
    result = gga_zda.read([("f", lines)])
    assert len(result.fixes) == 1 and not result.rejected_checksums
    assert result.fixes[0].day == date(2024, 6, 2)
    assert result.fixes[0].gga.lon == pytest.approx(-(83 + 12.6475 / 60))


def test_comma_layout_continuations():
    body = "GPZDA,000000.00,02,06,2024,00,00"
    lines = [
        f"2024-06-02T00:00:00Z,${body[:12]}",
        f"2024-06-02T00:00:00Z,{body[12:]}*{checksum(body):02X}",
    ]
    assert list(gga_zda.sentences(lines)) == [(1, f"${body}*{checksum(body):02X}")]


def test_a_split_sentence_isnt_joined_with_the_next_sentence():
    lines = ["1531596045.688 $GPZDA,00000", "1531596045.718 " + zda("000001.00", 9)]
    assert [text for _, text in gga_zda.sentences(lines)] == ["$GPZDA,00000", zda("000001.00", 9)]


def test_a_zda_outside_the_gps_era_is_skipped():
    result = gga_zda.read([("f", [nmea("GPZDA,000000.00,31,05,0002,00,00"), gga("000001.00")])])
    assert result.fixes == [] and "ZDA without a valid date" in result.errors[0]


# -- nav01 and nav24 read the same way (#81) --


def test_nav1_bare_nmea_without_checksums():
    """Melville's and Revelle's raw NMEA; checksums may be missing, and RR1802's file opens
    with the tail of a cut-off sentence
    """
    lines = [
        "SrJ:",
        "b12243.6025,W,D,9,2.1   ,18,,,*32",
        "$GPZDA,235600,24,05,2009,00,00*42",
        "$GPGGA,235601,2600.4311,N,12229.8443,E,2,7,0.4,45,M,,M",
        "$GPVTG,336.3,T,340.2,M,1.5,N,2.8",
        "$GPZDA,235601,24,05,2009,00,00*43",
        "$GPGGA,235602,2600.4315,N,12229.8441,E,2,7,0.4,45,M,,M",
    ]
    result = formats.get("nav01").reader.read([("gp150_20090524235600.raw", lines)])
    assert [(f.day, f.gga.time.seconds) for f in result.fixes] == [
        (date(2009, 5, 24), 86161.0),
        (date(2009, 5, 24), 86162.0),
    ]
    assert result.errors == [] and result.rejected_checksums == {}


def test_nav24_sikuliaq_lines():
    """Logger ID, ISO timestamp and sentence, tab-separated, after `#` header lines; the ZDA
    dates the GGA, not the logger's timestamp
    """
    lines = [
        "# Platform:",
        "#   Name: R/V Sikuliaq",
        "ins_seapath_position\t2025-08-30T00:00:00.0009Z\t$GPZDA,235959.97,29,08,2025,,*6F",
        (
            "ins_seapath_position\t2025-08-30T00:00:00.0874Z\t$GPGGA,235959.97,6429.644114,N,"
            "16526.297784,W,2,09,0.9,-0.32,M,7.66,M,0.8,1022*44"
        ),
        "ins_seapath_position\t2025-08-30T00:00:00.3082Z\t$PSXN,20,0,0,0,0*3B",
    ]
    result = formats.get("nav24").reader.read([("ins_seapath_position.20250830T0000Z", lines)])
    (fix,) = result.fixes
    assert (fix.day, fix.gga.time.seconds) == (date(2025, 8, 29), 86399.97)
    assert fix.gga.lat == pytest.approx(64 + 29.644114 / 60)
    assert result.errors == []


@pytest.mark.parametrize(
    "format_id, lines, expected",
    [
        (  # Healy's Seapath 330+, 2014: comma-separated (HLY14TF)
            "nav25",
            [
                "gnss_seapath_330,2014-09-14T23:59:56Z,$INZDA,235956.24,14,09,2014,,*75",
                "gnss_seapath_330,2014-09-14T23:59:56Z,$INGGA,235956.24,4735.432106,N,"
                + "12220.460931,W,1,12,0.7,-4.41,M,-18.47,M,,*64",
                "gnss_seapath_330,2014-09-14T23:59:56Z,$INHDT,269.35,T*1E",
            ],
            [(date(2014, 9, 14), 86396.24)],
        ),
        (  # Knorr, 2013: NAV tag, logger date and time, device tag
            "nav20",
            [
                "NAV 2013/11/20 05:00:00.644 GPS $GPGGA,050000.00,4131.43842,N,07040.33599,W,2,"
                + "8,0.9,29.17,M,-30.68,M,9,0907*6B",
                "NAV 2013/11/20 05:00:00.952 GPS $GPZDA,050000.00,20,11,2013,+0,+0*61",
            ],
            [(date(2013, 11, 20), 18000.0)],
        ),
        (  # year:day-of-year timestamps; ZDA's month unpadded; a GGA past midnight
            "nav12",
            [
                "cnav\t2010:258:00:00:00.0656\t$GPZDA,235959.00,14,9,2010,+0,+0*58",
                "cnav\t2010:258:00:00:00.6557\t$GPGGA,000000.00,2118.98294,N,15753.17430,W,2,"
                + "8,1.2,25.62,M,6.61,M,10,0108*4C",
                "cnav\t2010:258:00:00:01.0227\t$GPZDA,000000.00,15,9,2010,+0,+0*58",
            ],
            [(date(2010, 9, 15), 0.0)],
        ),
    ],
)
def test_logger_prefixes_before_gga_and_zda(format_id, lines, expected):
    """nav25, nav20 and nav12 differ from nav29 only in the prefix before each sentence"""
    result = formats.get(format_id).reader.read([("f", lines)])
    assert [(f.day, f.gga.time.seconds) for f in result.fixes] == expected
    assert result.errors == [] and result.rejected_checksums == {}


def test_nav27_gll_dated_by_zda():
    """Sproul's GP-90 logged GLL, not GGA (SP1531): GLL gives the fix, ZDA the date"""
    lines = [
        "$GPDTM,W84,,00.0000,N,00.0000,E,,W84*41",
        "$GPGLL,3241.7854,N,11709.3193,W,000001.00,A,A*76",
        "$GPVTG,321.5,T,308.9,M,0.0,N,0.0,K,A*24",
        "$GPZDA,000001.00,09,12,2015,-08,00*4E",
        "$GPGBS,000001.00,,,,,,,*6E",
    ]
    result = formats.get("nav27").reader.read([("gp90_20151209000000.raw", lines)])
    (fix,) = result.fixes
    assert (fix.day, fix.gga.time.seconds, fix.gga.quality) == (date(2015, 12, 9), 1.0, 1)
    assert fix.gga.nsat is None and fix.gga.hdop is None and fix.gga.alt is None
    assert result.errors == [] and result.rejected_checksums == {}
    # a GLL without a time is skipped and counted as such
    timeless = nmea("GPGLL,3241.7854,N,11709.3193,W,,A,A")
    result = formats.get("nav27").reader.read([("f", [timeless, lines[3]])])
    assert result.skipped == {"f": {"GLL without a time": 1}}
