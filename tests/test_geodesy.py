import numpy as np
import pandas as pd
import pytest
from conftest import DATA

from nav_qa_processing import geodesy, rawnav
from nav_qa_processing.quantities import COURSE_MADE_GOOD, SPEED_MADE_GOOD

# -- Distance and azimuth (pyproj) --


def test_known_geodesics():
    # one degree of latitude at the equator, and one of longitude along it
    distance, azimuth = geodesy.inverse([0.0, 0.0], [0.0, 0.0], [0.0, 1.0], [1.0, 0.0])
    np.testing.assert_allclose(distance, [110574.389, 111319.491], atol=1e-3)
    np.testing.assert_allclose(azimuth, [0.0, 90.0], atol=1e-9)


def test_coincident_and_missing_points():
    distance, azimuth = geodesy.inverse([10.0, np.nan], [20.0, 0.0], [10.0, 1.0], [20.0, 0.0])
    assert (distance[0], azimuth[0]) == (0.0, 0.0)  # pyproj alone would give 180
    assert np.isnan(distance[1]) and np.isnan(azimuth[1])


def test_azimuth_is_in_0_to_360_and_across_the_antimeridian():
    _, azimuth = geodesy.inverse(0, 0, -1e-3, -1e-3)
    assert 180 < azimuth < 270
    # 0.2° of longitude across the antimeridian, both ways
    distance, azimuth = geodesy.inverse([179.9, -179.9], 10.0, [-179.9, 179.9], 10.0)
    np.testing.assert_allclose(distance, 21_927.872, atol=1e-3)
    np.testing.assert_allclose(azimuth, [89.983, 270.017], atol=1e-3)


# -- Segments, acceleration and per-record SOG/COG --

T = np.array([0.0, 1.0, 2.0, 2.0, 4.0])
LON = np.array([0.0, 1e-5, 2e-5, 3e-5, 4e-5])
LAT = np.zeros(5)


def test_segments_speed_course_and_midpoint_time():
    seg = geodesy.segments(T, LON, LAT)
    metres = geodesy.inverse(0, 0, 1e-5, 0)[0]
    assert seg.speed[0] == pytest.approx(metres)
    assert np.isnan(seg.speed[2])  # no elapsed time
    assert seg.speed[3] == pytest.approx(metres / 2)
    np.testing.assert_allclose(seg.course, 90.0)
    np.testing.assert_allclose(seg.time, [0.5, 1.5, 2.0, 3.0])


def test_accelerations_between_segment_midpoints():
    acceleration, time = geodesy.accelerations([0.5, 1.5, 3.5], [1.0, 2.0, 1.0])
    np.testing.assert_allclose(acceleration, [1.0, -0.5])
    np.testing.assert_allclose(time, [1.0, 2.5])


def test_times_as_datetime64_or_pandas():
    times = pd.to_datetime(["2026-07-09T15:11:57.5Z", "2026-07-09T15:11:58Z"], format="ISO8601")
    assert np.diff(geodesy.epoch_seconds(times))[0] == pytest.approx(0.5)
    assert np.diff(geodesy.epoch_seconds(pd.Series(times)))[0] == pytest.approx(0.5)


def test_sog_cog_last_good_fix_repeats_the_one_before():
    t = np.arange(4.0)
    sog, cog = geodesy.sog_cog(t, [0, 1e-5, 3e-5, 6e-5], [0, 0, 0, 0])
    assert sog[2] == sog[3] and cog[2] == cog[3]
    assert sog[0] < sog[1] < sog[2]


def test_sog_cog_skips_flagged_fixes():
    t = np.arange(4.0)
    lon = [0, 1e-5, 5.0, 2e-5]  # the third fix is a jump, flagged
    sog, _ = geodesy.sog_cog(t, lon, [0, 0, 0, 0], good=[True, True, False, True])
    assert np.isnan(sog[2])
    # the second fix's segment runs to the fourth, over 2 s
    assert sog[1] == pytest.approx(geodesy.inverse(1e-5, 0, 2e-5, 0)[0] / 2)


def test_sog_cog_with_fewer_than_two_good_fixes():
    sog, cog = geodesy.sog_cog([0.0, 1.0], [0, 1e-5], [0, 0], good=[True, False])
    assert np.isnan(sog).all() and np.isnan(cog).all()


def test_navmanager_buffer_uses_the_previous_segments_distance():
    """Buffers of 4 records over 9: two full buffers, then the end-of-file pass"""
    t = np.arange(9.0)
    lon = np.cumsum(np.arange(9) * 1e-5)
    lat = np.zeros(9)
    distance = geodesy.inverse(lon[:-1], 0, lon[1:], 0)[0]
    sog, cog = geodesy.sog_cog(t, lon, lat, navmanager_buffer=4)
    correct, correct_cog = geodesy.sog_cog(t, lon, lat)
    # buffer 1 = records 0-3 (segments 0-2); its last fix starts buffer 2 = records 3-6
    # (segments 3-5); records 6-8 go to the end-of-file pass and are computed correctly
    expected = np.concatenate(([0.0], distance[:5], correct[6:9]))
    np.testing.assert_allclose(sog, expected)
    np.testing.assert_allclose(cog, correct_cog)


def test_a_buffer_exactly_full_at_the_end_is_still_a_full_buffer():
    """navsogcog's input ends in a newline, so a buffer filled by the last record is processed
    as a full buffer
    """
    # buffer 1 = records 0-3, buffer 2 = records 3-6, which ends with the last record
    stale = geodesy._full_buffer_segments(np.arange(7), 7, 4)
    assert stale.tolist() == [True] * 6


def test_speeds_are_correct_by_default_on_long_series():
    """More than 86,400 records: the default doesn't reproduce navsogcog's buffer bug"""
    n = geodesy.NAVSOGCOG_BUFFER + 10
    t = np.arange(n, dtype=float)
    lon = np.cumsum(np.where(np.arange(n) % 2, 1e-5, 2e-5))  # alternating step lengths
    sog, _ = geodesy.sog_cog(t, lon, np.zeros(n))
    seg = geodesy.segments(t, lon, np.zeros(n))
    np.testing.assert_allclose(sog[:-1], seg.speed)


def test_navmanager_buffer_counts_flagged_records():
    good = np.array([True, False, False, True, True, True])
    stale = geodesy._full_buffer_segments(np.flatnonzero(good), 6, 4)
    # buffer 1 = records 0-3: one segment (0→3), then records 3-5 go to the end-of-file pass
    assert stale.tolist() == [True, False, False]


def test_as_written_rounds_like_text():
    np.testing.assert_array_equal(
        geodesy.as_written([1.23456789016, -2.5, 0.125], 10), [1.2345678902, -2.5, 0.125]
    )
    assert geodesy.as_written([2.675], 2)[0] == 2.67  # 2.675 is 2.67499999… in binary


# -- Parity with R2R's published bestres --

FETCH = "uv run scripts/fetch_r2r_fileset.py {cruise} {fileset}"


def _bestres(cruise, fileset, start=None, end=None):
    directory = DATA / f"{cruise}_{fileset}_gnss"
    product = directory / "products" / "data" / f"{cruise}_bestres.geoCSV"
    if not product.exists():
        pytest.skip(
            f"{cruise} not downloaded; run: {FETCH.format(cruise=cruise, fileset=fileset)}"
        )
    window = {} if start is None else {"start": start, "end": end}
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29", **window)
    return nav, pd.read_csv(product, comment="#", dtype=str)


def _check_parity(nav, r2r):
    f = nav.frame
    assert len(f) == len(r2r)
    lon = geodesy.as_written(f["lon"], nav.decimals["lon"])
    lat = geodesy.as_written(f["lat"], nav.decimals["lat"])
    # R2R carries navsogcog's buffer bug, so reproduce it to compare
    sog, cog = geodesy.sog_cog(f["time"], lon, lat, navmanager_buffer=geodesy.NAVSOGCOG_BUFFER)
    ours_sog = np.array([float(SPEED_MADE_GOOD.format(v)) for v in sog])
    sog_off = np.abs(ours_sog - r2r["speed_made_good"].astype(float).to_numpy())
    # R2R used NavManager's Vincenty; pyproj differs by at most ~1e-5 m s-1, so a speed may
    # round the other way only when it sits within that of a rounding half
    near_half = np.abs((sog * 100) % 1 - 0.5) < 1e-3
    assert (sog_off[~near_half] < 1e-9).all()
    assert (sog_off[near_half] < 0.0101).all()

    ours_cog = np.array([float(COURSE_MADE_GOOD.format(v)) for v in cog])
    theirs = r2r["course_made_good"].astype(float).to_numpy()
    turn = np.abs((ours_cog - theirs + 180) % 360 - 180)
    # Course is as sensitive to position as 1/step: allow 0.001° plus 0.003°·m / step, and
    # ignore steps under a millimetre (at the dock), where course is noise
    step = np.append(geodesy.segments(f["time"], lon, lat).distance, np.inf)
    step[-1] = step[-2]  # the last record repeats the previous segment
    checked = step >= 1e-3
    assert (turn[checked] <= 0.0011 + 0.003 / step[checked]).all()


def test_sp2613_sog_cog_match_r2r():
    _check_parity(
        *_bestres(
            "SP2613",
            169851,
            pd.Timestamp("2026-07-09", tz="UTC"),
            pd.Timestamp("2026-07-10", tz="UTC"),
        )
    )


def test_sp2424_sog_cog_match_r2r_across_the_navsogcog_buffer():
    """150,924 records: R2R's first 86,399 speeds carry navsogcog's buffer bug"""
    _check_parity(*_bestres("SP2424", 161900))
