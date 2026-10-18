"""The nav03 reader: NOAA SCS's GLL and occasional GGA, timed by the logger (#99)."""

from datetime import date

import pytest
from conftest import DATA

from nav_qa_processing import formats, rawnav
from nav_qa_processing.formats.nmea import checksum


def read(lines):
    return formats.get("nav03").reader.read([("NEMA-Serial_20100916-131207.Raw", lines)])


def test_gll_cut_after_the_longitude_takes_the_logger_time():
    """Blue Heron's GLLs have no time, status or checksum: the logger's date and time, without
    a quality; VTG lines between them are passed over
    """
    result = read(
        [
            "09/16/2010,13:12:09.546,$GPGLL,4646.622,N,09204.105,W",
            "09/16/2010,13:12:19.640,$GPVTG,122,T,123,M,8.9,N,16.4,K",
            "09/16/2010,13:12:49.531,$GPGLL,4646.570,N,09203.984,W",
        ]
    )
    fixes = [(f.day, f.gga.time.seconds, f.gga.time.decimals, f.gga.quality) for f in result.fixes]
    assert fixes == [
        (date(2010, 9, 16), 47529.546, 3, None),
        (date(2010, 9, 16), 47569.531, 3, None),
    ]
    assert result.fixes[0].gga.lat == pytest.approx(46 + 46.622 / 60)
    assert result.errors == []


def test_a_gga_is_timed_by_the_logger_too():
    """One clock for the stream: the logger's, not the GGA's own (here a second behind)"""
    body = "GPGGA,131208,4646.622,N,09204.105,W,1,08,1.0,183,M,-31,M,,"
    result = read([f"09/16/2010,13:12:09.546,${body}*{checksum(body):02X}"])
    (fix,) = result.fixes
    assert (fix.gga.time.seconds, fix.gga.quality, fix.gga.nsat) == (47529.546, 1, 8)


def test_a_gll_without_a_position_is_skipped():
    result = read(["09/16/2010,13:12:09.546,$GPGLL,,,,"])
    assert result.fixes == [] and result.skipped == {
        "NEMA-Serial_20100916-131207.Raw": {"sentence without a position": 1}
    }


def test_bh10_17():
    """Blue Heron on Lake Superior, 2010-09-16 to 17: 6,299 GLLs and 231 GGAs, all read, in
    time order. R2R's products for it couldn't be fetched when this was written (#99)
    """
    path = DATA / "BH10-17_105061_gnss" / "data" / "NEMA-Serial_20100916-131207.Raw"
    if not path.exists():
        pytest.skip(
            "BH10-17 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset BH10-17 105061"
        )
    nav = rawnav.read_fileset([path], "nav03")
    frame = nav.frame
    assert nav.errors == [] and len(frame) == 6_530
    assert int(frame["quality"].isna().sum()) == 6_299 and (frame["quality"].dropna() == 1).all()
    assert (frame["time"].diff().dropna().dt.total_seconds() > 0).all()
    assert -92.1 < frame["lon"].min() and frame["lon"].max() < -89.8
