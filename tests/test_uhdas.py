"""The UHDAS readers: GGA dated by PYRTM (nav31) or UNIXD (uhdas) (#97)."""

from datetime import date

import pytest

from nav_qa_processing import formats, rawnav
from nav_qa_processing.formats import uhdas
from nav_qa_processing.formats.nmea import checksum


def gga(hhmmss: str) -> str:
    body = f"GPGGA,{hhmmss},4648.9699,N,09158.8461,W,1,9,1.6,196,M,,M,,"
    return f"${body}*{checksum(body):02X}"


def dated(format_id: str, name: str, lines: list[str]):
    result = formats.get(format_id).reader.read([(name, lines)])
    return [(f.day, f.gga.time.seconds) for f in result.fixes], result


def test_the_decimal_day_counts_from_zero_at_january_first():
    """Day 275.83 of 2019 is October 3, 20:00 (BH19-30, which R2R dates the same)"""
    day, ns = uhdas.clock(["$PYRTM", "2019", "275.8333405", "75.8928819"], None)
    assert day == date(2019, 10, 3) and ns / 1e9 == pytest.approx(72000.6, abs=0.1)
    assert uhdas.clock(["$UNIXD", "0.5", "2.1"], 2016)[0] == date(2016, 1, 1)
    assert uhdas.clock(["$UNIXD", "365.5", "2.1"], 2015)[0] == date(2016, 1, 1)  # past the year
    assert uhdas.clock(["$UNIXD", "0.5", "2.1"], None) is None  # no year


def test_name_year():
    assert uhdas.name_year("raw/simrad/km2015_323_07200.gps") == 2015
    assert uhdas.name_year("bh2019_275_72000.gps") == 2019
    assert uhdas.name_year("simrad.gps") is None


def test_nav31_gga_dated_by_pyrtm():
    """The UHDAS clock runs about 4 minutes ahead of the GPS: the date comes from PYRTM, the
    time from GGA. A GGA before the first PYRTM is dated by the one after it
    """
    lines = [gga("195549"), "$PYRTM,2019,275.8333405,75.8928819", gga("195550")]
    fixes, result = dated("nav31", "bh2019_275_72000.gps", lines)
    assert fixes == [(date(2019, 10, 3), 71749.0), (date(2019, 10, 3), 71750.0)]
    assert result.errors == []


def test_uhdas_gga_dated_by_unixd_and_the_year_in_the_file_name():
    lines = [
        "$UNIXD,323.0833359,9.3380169",
        gga("020000.00"),
        "$UNIXD,323.9999,9.4",
        gga("235959.99"),
    ]
    lines += ["$UNIXD,324.00001,9.4", gga("000000.80")]
    fixes, _ = dated("uhdas", "km2015_323_07200.gps", lines)
    assert [d for d, _ in fixes] == [date(2015, 11, 20), date(2015, 11, 20), date(2015, 11, 21)]


def test_a_uhdas_file_without_a_year_in_its_name_is_unread(tmp_path):
    path = tmp_path / "simrad.gps"
    path.write_text("$UNIXD,323.0833359,9.3380169\n" + gga("020000.00") + "\n")
    nav = rawnav.read_fileset([path], "uhdas")
    assert list(nav.unread_files) == ["simrad.gps"]
