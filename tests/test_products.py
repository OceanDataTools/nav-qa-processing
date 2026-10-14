from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import DATA, FIXTURES

from nav_qa_processing import products, qc, rawnav

START = pd.Timestamp("2026-07-09T12:00:00Z")


def best(seconds, flags=None, lon=None):
    """A bestres-like frame with records at `seconds` after 12:00:00"""
    seconds = np.asarray(seconds, dtype=float)
    n = len(seconds)
    return pd.DataFrame(
        {
            "time": START + pd.to_timedelta(seconds, unit="s"),
            "lon": np.linspace(-117.0, -116.9, n) if lon is None else lon,
            "lat": np.full(n, 32.0),
            "sog": np.full(n, 3.0),
            "cog": np.full(n, 90.0),
            "flags": np.zeros(n, dtype=np.uint16) if flags is None else flags,
        }
    )


def picked(frame, interval=60.0):
    return list((products.sample(frame, interval)["time"] - START).dt.total_seconds())


# -- sample (1min) --


def test_records_on_the_boundary_are_kept():
    assert picked(best(np.arange(0, 181, 0.5))) == [0, 60, 120, 180]


def test_nearest_record_within_half_an_interval():
    # receiver logging at x.5 s: the record half a second before the boundary is as near as
    # the one after; the tie goes to the earlier record
    assert picked(best(np.arange(0.5, 181, 1.0))) == [0.5, 59.5, 119.5, 179.5]
    assert picked(best([10, 50, 70, 130])) == [10, 50, 130]  # 50 is nearer to 60 than 70


def test_a_gap_longer_than_the_interval_skips_boundaries():
    # 60 and 120 have no record within 30 s; 200 is the nearest to 180, 20 s away
    assert picked(best([0, 1, 200, 201])) == [0, 200]


def test_a_flagged_record_on_the_boundary_gives_way_to_the_nearest_good_one():
    flags = np.zeros(7, dtype=np.uint16)
    flags[3] = qc.Flag.ACCELERATION  # 60 s
    assert picked(best([0, 20, 59, 60, 62, 100, 120], flags=flags)) == [0, 59, 120]


def test_boundaries_just_outside_the_fixes_count():
    """SP2424's 1min starts with its first fix, 06:19:01, for 06:19:00"""
    assert picked(best([1, 30, 50])) == [1, 50]


def test_a_cruise_shorter_than_an_interval():
    assert picked(best([10, 20])) == [10]  # nearest to 0, within 30 s; 60 is 40 s away
    assert picked(best([35, 40])) == [40]  # 0 is too far; 40 is the nearer to 60
    assert picked(best([], flags=np.zeros(0, dtype=np.uint16))) == []


def test_sample_keeps_bestres_sog_and_cog():
    frame = best([0, 60])
    frame["sog"] = [1.5, 2.5]
    one = products.sample(frame)
    assert list(one.columns) == ["time", "lon", "lat", "sog", "cog"]
    assert one["sog"].tolist() == [1.5, 2.5]


# -- control --


def test_a_straight_track_keeps_its_ends():
    one = best(np.arange(0, 600, 60))
    assert len(products.control(one)) == 2


def test_control_keeps_corners_larger_than_the_tolerance():
    lon = np.array([-117.0, -116.9, -116.8, -116.8, -116.8])
    lat = np.array([32.0, 32.0, 32.0, 32.1, 32.2])
    one = best(np.arange(0, 300, 60), lon=lon)
    one["lat"] = lat
    assert products.control(one)["lon"].tolist() == [-117.0, -116.8, -116.8]


def test_control_across_the_antimeridian():
    """Unwrapped, an eastbound track across 180° is a straight line"""
    lon = np.array([179.8, 179.9, -180.0, -179.9, -179.8])
    one = best(np.arange(0, 300, 60), lon=lon)
    assert products.control(one)["lon"].tolist() == [179.8, -179.8]


def test_control_of_very_short_tracks():
    assert len(products.control(best([0, 60]))) == 2
    assert len(products.control(best([0]))) == 1


# -- bestres --


def test_bestres_carries_flags_and_no_sog_for_flagged_fixes():
    times = START + pd.to_timedelta(np.arange(4), unit="s")
    frame = pd.DataFrame(
        {
            "time": times,
            "lon": [-117.0, -117.00001, -117.00002, -117.00003],
            "lat": [32.0] * 4,
            "quality": pd.array([2] * 4, dtype="Int64"),
            "nsat": pd.array([12] * 4, dtype="Int64"),
            "hdop": [0.8] * 4,
            "alt": [7.0] * 4,
        }
    )
    nav = rawnav.RawNav(frame, {"time": 0, "lon": 5, "lat": 5, "hdop": 1, "alt": 1}, ["x"])
    flags = np.array([0, 0, int(qc.Flag.BAD_FIX), 0], dtype=np.uint16)
    out = products.bestres(nav, qc.QCResult(flags, 1.0))
    assert out["flags"].tolist() == [0, 0, int(qc.Flag.BAD_FIX), 0]
    assert np.isnan(out["sog"][2]) and not np.isnan(out["sog"][1])


# -- Comparison with R2R --


def _products(cruise, fileset, **window):
    directory = DATA / f"{cruise}_{fileset}_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset {cruise} {fileset}"
        )
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29", **window)
    one = products.sample(products.bestres(nav, qc.flag(nav)))
    return directory, one, products.control(one)


def _read(path):
    frame = pd.read_csv(path, comment="#", dtype=str)
    frame["iso_time"] = pd.to_datetime(frame["iso_time"])
    return frame


def test_sp2613_1min_and_control_match_r2r():
    _, one, ctl = _products(
        "SP2613",
        169851,
        start=datetime(2026, 7, 9, tzinfo=UTC),
        end=datetime(2026, 7, 10, tzinfo=UTC),
    )
    r2r = _read(FIXTURES / "SP2613_1min.geoCSV")
    assert len(one) == len(r2r) == 475
    assert (one["time"].to_numpy() == r2r["iso_time"].to_numpy()).all()
    for ours, theirs in [("lon", "ship_longitude"), ("lat", "ship_latitude")]:
        written = one[ours].map(lambda v: float(f"{v:.10f}")).to_numpy()
        assert (written == r2r[theirs].astype(float).to_numpy()).all()
    assert np.abs(one["sog"].round(2) - r2r["speed_made_good"].astype(float)).max() < 0.0101
    r2r_control = _read(FIXTURES / "SP2613_control.geoCSV")
    assert (ctl["time"].to_numpy() == r2r_control["iso_time"].to_numpy()).all()


def test_sp2424_1min_and_control_match_r2r_but_for_the_final_fix():
    """We keep SP2424's final fix (03:16:44.50Z), which NavManager drops (#14); it is the
    nearest record to 03:17:00, so it ends our 1min and control; everything else is equal
    """
    directory, one, ctl = _products("SP2424", 161900)
    r2r = _read(directory / "products" / "data" / "SP2424_1min.geoCSV")
    final = pd.Timestamp("2024-11-04T03:16:44.50Z")
    assert len(one) == len(r2r) + 1 and one["time"].iloc[-1] == final
    assert (one["time"].iloc[:-1].to_numpy() == r2r["iso_time"].to_numpy()).all()
    r2r_control = _read(directory / "products" / "data" / "SP2424_control.geoCSV")
    assert (ctl["time"].iloc[:-1].to_numpy() == r2r_control["iso_time"].iloc[:-1].to_numpy()).all()
    assert ctl["time"].iloc[-1] == final
