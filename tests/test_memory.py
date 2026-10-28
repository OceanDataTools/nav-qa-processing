"""A fileset is read one file at a time into columns, and the whole-cruise steps after it work a
chunk at a time, with the same results (#157: a 20-day cruise at 2 Hz ran a 4 GB OpenVDM
server out of memory).
"""

import tracemalloc
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import shapely
from test_gga_zda import nmea

from nav_qa_processing import formats, geocsv, geodesy, plots, qc, rawnav
from nav_qa_processing.formats import catalog
from nav_qa_processing.formats.gga_zda import Fixes, SpanCache
from nav_qa_processing.formats.nmea import GGA, TimeOfDay
from nav_qa_processing.land import Land


def _names(format_id: str) -> list[str]:
    """Two file names the format's reader can date: UHDAS's year, nav22's stamp"""
    if format_id == "uhdas":
        return ["km2015_321_72000.gps", "km2015_321_72001.gps"]
    return ["a_20260709120000.txt", "b_20260709120000.txt"]


@pytest.mark.parametrize("format_id", sorted(formats.READERS))
def test_readers_read_their_files_once_in_turn(format_id):
    """rawnav hands a reader its files as they're read, a generator it can go through once"""
    reader = formats.READERS[format_id]
    examples = list(catalog.formats()[format_id].examples)
    files = [(name, examples) for name in _names(format_id)]
    from_list = reader.read(files)
    from_generator = reader.read(file for file in files)
    assert len(from_generator.fixes) > 0
    assert list(from_generator.fixes) == list(from_list.fixes)
    assert from_generator.errors == from_list.errors


def _gga(lat=32.7, lon=-117.2, quality=2, nsat=12, hdop=0.8, alt=7.36, talker="GP", ns=0):
    return GGA(TimeOfDay(ns, 2), lat, lon, 6, 6, quality, nsat, hdop, 1, alt, 2, talker)


def test_fixes_keep_what_a_fix_holds():
    fixes = Fixes()
    fixes.append(_gga(), "a.txt", 3, date(2026, 7, 9))
    fixes.append(_gga(lat=None, quality=None, nsat=None, hdop=None, talker="GN"), "b.txt", 9)
    assert len(fixes) == 2 and fixes.files == ["a.txt", "b.txt"]
    first, second = fixes
    assert (first.gga, first.source_file, first.line, first.day) == (
        _gga(),
        "a.txt",
        3,
        date(2026, 7, 9),
    )
    assert second.gga == _gga(lat=None, quality=None, nsat=None, hdop=None, talker="GN")
    assert second.day is None and fixes[-1] == second
    with pytest.raises(IndexError):
        fixes[2]


def test_fixes_extend_renumbers_files_and_talkers():
    ours, theirs = Fixes(), Fixes()
    ours.append(_gga(talker="GP"), "a.txt", 1, date(2026, 7, 9))
    theirs.append(_gga(talker="GN"), "b.txt", 1, date(2026, 7, 9))
    theirs.append(_gga(talker="GP"), "a.txt", 2, date(2026, 7, 9))
    ours += theirs
    assert [(f.source_file, f.gga.talker, f.line) for f in ours] == [
        ("a.txt", "GP", 1),
        ("b.txt", "GN", 1),
        ("a.txt", "GP", 2),
    ]


def test_fixes_drain_into_arrays():
    fixes = Fixes()
    fixes.append(_gga(ns=3_600 * 10**9), "a.txt", 1, date(2026, 7, 9))
    columns = fixes.drain()
    assert len(fixes) == 0  # handed on, not held twice
    assert pd.Timestamp(columns["time"][0], unit="ns") == pd.Timestamp("2026-07-09T01:00:00")
    assert "day" not in columns and "time_ns" not in columns


def test_span_cache_tells_files_of_one_name_apart():
    """Keyed by name, length and first and last lines: the same name in another directory, or
    a file a logger added to since its span, is parsed again rather than mistaken
    """
    cache = SpanCache()
    cache.keep("gps.txt", ["a", "b"], "first")
    assert cache.take("gps.txt", ["a", "c"]) is None
    assert cache.take("gps.txt", ["a", "b", "c"]) is None
    assert cache.take("gps.txt", ["a", "b"]) == "first"


def _stamped(start: datetime, count: int) -> list[str]:
    """nav33 lines, one a second"""
    lines = []
    for i in range(count):
        t = start + timedelta(seconds=i)
        lat = 3242 + i * 1e-4
        body = f"GPGGA,{t:%H%M%S}.00,{lat:.4f},N,11712.0000,W,2,12,0.8,7.36,M,-35.6,M,,"
        lines.append(f"{t:%Y-%m-%dT%H:%M:%S}.000000Z,{nmea(body)}")
    return lines


def test_reading_takes_about_a_hundred_bytes_a_fix(tmp_path):
    """Memory Python and numpy allocate while reading 20,000 fixes in 10 files: about 90 bytes
    a fix in columns, a file's lines at a time and the frame; 1.1 KB a fix before #157
    """
    start = datetime(2026, 7, 9, tzinfo=UTC)
    for day in range(10):
        lines = _stamped(start + timedelta(hours=day), 2_000)
        (tmp_path / f"gps-{day:02d}.txt").write_text("\n".join(lines) + "\n")
    tracemalloc.start()
    try:
        nav = rawnav.read_fileset(sorted(tmp_path.iterdir()), "nav33")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(nav.frame) == 20_000
    assert peak / len(nav.frame) < 400  # [bytes]


def test_inverse_in_chunks_matches(monkeypatch):
    rng = np.random.default_rng(1)
    lon1, lon2 = rng.uniform(-180, 180, (2, 1001))
    lat1, lat2 = rng.uniform(-80, 80, (2, 1001))
    lat1[5] = np.nan
    whole = geodesy.inverse(lon1, lat1, lon2, lat2)
    monkeypatch.setattr(geodesy, "CHUNK", 100)
    chunked = geodesy.inverse(lon1, lat1, lon2, lat2)
    for a, b in zip(whole, chunked, strict=True):
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("signed", [False, True])
def test_accelerations_in_chunks_match(monkeypatch, signed):
    rng = np.random.default_rng(2)
    t = np.cumsum(rng.uniform(0.5, 1.5, 1001))
    lon = -117 + np.cumsum(rng.normal(0, 1e-5, 1001))
    lat = 32 + np.cumsum(rng.normal(0, 1e-5, 1001))
    whole = qc.acceleration_at_fixes(t, lon, lat, 5.0, signed=signed)
    monkeypatch.setattr(geodesy, "CHUNK", 100)
    np.testing.assert_array_equal(qc.acceleration_at_fixes(t, lon, lat, 5.0, signed=signed), whole)


def test_inland_in_chunks_matches(monkeypatch):
    island = Land(shapely.box(-1, -1, 1, 1))
    rng = np.random.default_rng(3)
    lon, lat = rng.uniform(-2, 2, (2, 1001))
    whole = island.inland(lon, lat)
    monkeypatch.setattr(geodesy, "CHUNK", 100)
    np.testing.assert_array_equal(island.inland(lon, lat), whole)


def test_products_written_in_chunks_match(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2026-07-09", periods=25, freq="1s", tz="UTC"),
            "lon": np.linspace(-117.2, -117.1, 25),
            "lat": np.linspace(32.7, 32.8, 25),
            "quality": pd.array([2] * 25, dtype="Int64"),
            "nsat": pd.array([12] * 25, dtype="Int64"),
            "hdop": [0.8] * 25,
            "alt": [7.36] * 25,
            "sog": [5.1] * 25,
            "cog": [90.0] * 25,
            "flags": [0, 4] * 12 + [0],
        }
    )
    decimals = {"time": 2, "lon": 6, "lat": 6, "hdop": 1, "alt": 2}
    source = geocsv.Source("X")
    created = datetime(2026, 7, 9, tzinfo=UTC)
    (tmp_path / "whole").mkdir()
    (tmp_path / "chunked").mkdir()
    whole = geocsv.write(tmp_path / "whole", geocsv.BESTRES, frame, decimals, source, created)
    monkeypatch.setattr(geocsv, "CHUNK", 4)
    chunked = geocsv.write(tmp_path / "chunked", geocsv.BESTRES, frame, decimals, source, created)
    assert chunked.read_text() == whole.read_text()
    assert whole.read_text().count("\n#20") == 12  # the flagged records


def test_long_track_simplified_a_stretch_at_a_time(monkeypatch):
    x = np.linspace(0, 1, 1001)
    lat = 0.01 * np.sin(x * 20)
    whole = plots._simplified(x, lat, 1e-4)
    line = shapely.simplify(shapely.LineString(np.column_stack([x, lat])), 1e-4)
    np.testing.assert_array_equal(np.column_stack(whole), np.asarray(line.coords))
    monkeypatch.setattr(plots, "SIMPLIFY_POINTS", 100)
    stretches = np.column_stack(plots._simplified(x, lat, 1e-4))
    assert tuple(stretches[0]) == (x[0], lat[0]) and tuple(stretches[-1]) == (x[-1], lat[-1])
    assert (np.diff(stretches[:, 0]) > 0).all()  # joined once, in order
    track = shapely.LineString(np.column_stack([x, lat]))
    assert shapely.hausdorff_distance(shapely.LineString(stretches), track) <= 1e-4
