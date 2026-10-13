from datetime import date

from nav_qa_processing.formats import gga_zda
from nav_qa_processing.formats.nmea import checksum


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}" if checksum(body) >= 0x10 else f"${body}"


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


def test_repeat_of_last_dated_gga_is_dropped():
    lines = [gga("120000.00"), zda("120000.01", 9), gga("120000.00"), gga("120001.00")]
    lines.append(zda("120001.01", 9))
    assert [t for _, t in dated(lines)] == ["43200", "43201"]


def test_trailing_buffer_drops_its_last_gga():
    lines = [zda("120000.00", 9), gga("120001.00"), gga("120002.00"), gga("120003.00")]
    assert [t for _, t in dated(lines)] == ["43201", "43202"]


def test_buffer_carries_across_files():
    result = gga_zda.read([("a", [gga("120000.00")]), ("b", [zda("120000.01", 9)])])
    assert [(f.source_file, f.day) for f in result.fixes] == [("a", date(2026, 7, 9))]


def test_no_zda_is_an_error_not_an_exception():
    result = gga_zda.read([("f", [gga("120000.00")])])
    assert result.fixes == [] and "no ZDA" in result.errors[0]


def test_invalid_zda_is_recorded_and_skipped():
    result = gga_zda.read([("f", [gga("120000.00"), nmea("GPZDA,120000.01,,,,,")])])
    assert "ZDA without a valid date" in result.errors[0]
