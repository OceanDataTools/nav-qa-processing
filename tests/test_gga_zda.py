from datetime import date
from itertools import pairwise

import pytest

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


def test_invalid_zda_is_recorded_and_skipped():
    result = gga_zda.read([("f", [gga("120000.00"), nmea("GPZDA,120000.01,,,,,")])])
    assert "ZDA without a valid date" in result.errors[0]


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
