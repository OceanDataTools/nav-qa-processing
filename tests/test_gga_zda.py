from datetime import date

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
