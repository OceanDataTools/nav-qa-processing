"""The nav12 reader: GGA dated by ZDA, or by the logger's year:day-of-year timestamp in a file
without ZDA (#81 follow-up: Langseth's C-Nav 3050, MGL1509)."""

from datetime import date

from nav_qa_processing import formats
from nav_qa_processing.formats import zda_or_stamp
from nav_qa_processing.formats.nmea import checksum


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}"


def gga(hhmmss: str) -> str:
    return nmea(f"GNGGA,{hhmmss},4012.4085,N,07341.8625,W,2,19,0.6,-13.4,M,0.0,M,2.0,0446")


def line(stamp: str, sentence: str) -> str:
    return f"cnav3050\t{stamp}\t{sentence}"


def dated(files):
    result = formats.get("nav12").reader.read(files)
    return [(f.day, f.gga.time.seconds, f.source_file) for f in result.fixes]


def test_doy_time():
    found = zda_or_stamp.doy_time(line("2015:141:00:00:01.0631", gga("000001.00")))
    assert found == (date(2015, 5, 21), round(1.0631e9))
    assert zda_or_stamp.doy_time(line("2015:366:00:00:01.0", "$GNGGA")) is None  # not a leap year
    assert zda_or_stamp.doy_time("2015-05-21T00:00:01Z,$GNGGA") is None


def test_a_file_without_zda_is_dated_by_its_logger_timestamps():
    """A GGA for 23:59:59 logged just after midnight is dated the day before"""
    lines = [
        line("2015:141:23:59:59.9", gga("235959.00")),
        line("2015:142:00:00:00.1", gga("235959.50")),
    ]
    lines.append(line("2015:142:00:00:01.0", gga("000000.50")))
    assert [(d, s) for d, s, _ in dated([("MGL-cnav3050.y2015d141", lines)])] == [
        (date(2015, 5, 21), 86399.0),
        (date(2015, 5, 21), 86399.5),
        (date(2015, 5, 22), 0.5),
    ]
    first, last = formats.get("nav12").reader.span(lines)
    assert (first.date(), last.date()) == (date(2015, 5, 21), date(2015, 5, 22))


def test_files_with_zda_are_dated_by_zda():
    """The ZDA, not the logger's (here a day off) timestamp, dates a file that has one"""
    with_zda = [
        line("2010:259:00:00:00.0", nmea("GPZDA,000000.00,15,9,2010,+0,+0")),
        line(
            "2010:259:00:00:00.6",
            nmea("GPGGA,000000.50,2118.98294,N,15753.17430,W,2,8,1.2,25.62,M,6.61,M,10,0108"),
        ),
    ]
    without = [line("2010:258:00:00:01.0", gga("000001.00"))]
    assert dated([("a", with_zda), ("b", without)]) == [
        (date(2010, 9, 15), 0.5, "a"),
        (date(2010, 9, 15), 1.0, "b"),
    ]
