from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from conftest import DATA

from nav_qa_processing import qc, rawnav
from nav_qa_processing.config import Thresholds
from nav_qa_processing.qc import Flag

START = pd.Timestamp("2026-07-09T12:00:00Z")
METRE = 1 / 111_320  # [degree] of latitude


def track(n=60, step=1.0, speed=3.0, decimals=10, **columns):
    """A ship steaming north at `speed` m/s, one fix every `step` s, all fixes good"""
    times = START + pd.to_timedelta(np.arange(n) * step, unit="s")
    lat = 32.0 + np.arange(n) * speed * step * METRE
    frame = pd.DataFrame(
        {
            "time": times,
            "lon": np.full(n, -117.0),
            "lat": lat,
            "quality": pd.array([2] * n, dtype="Int64"),
            "nsat": pd.array([12] * n, dtype="Int64"),
            "hdop": np.full(n, 0.8),
            "alt": np.full(n, 7.0),
        }
    )
    for name, values in columns.items():
        frame[name] = values
    decimals = {"time": 2, "lon": decimals, "lat": decimals, "hdop": 1, "alt": 3}
    return rawnav.RawNav(frame, decimals, ["synthetic"])


def flagged(nav, **kwargs):
    result = qc.flag(nav, **kwargs)
    return {int(i): Flag(int(result.flags[i])) for i in np.flatnonzero(result.flags)}, result


def test_a_clean_track_has_no_flags():
    flags, result = flagged(track())
    assert flags == {} and result.notes == []


def test_dates_outside_gps_dates_or_the_cruise_window():
    nav = track(5)
    nav.frame.loc[1, "time"] = pd.Timestamp("1990-01-01T00:00:00Z")
    flags, _ = flagged(nav)
    assert flags[1] & Flag.DATE_RANGE
    start = datetime(2026, 9, 1, tzinfo=UTC)  # the cruise is more than 30 days after the data
    flags, _ = flagged(track(5), start=start)
    assert all(f & Flag.DATE_RANGE for f in flags.values()) and len(flags) == 5
    flags, _ = flagged(track(5), start=start - timedelta(days=60))
    assert flags == {}


def test_null_island_at_any_time():
    nav = track(5)
    nav.frame.loc[2, ["lon", "lat"]] = 0.0
    flags, _ = flagged(nav)
    assert flags[2] & Flag.NULL_ISLAND


def test_missing_or_out_of_range_position():
    nav = track(6)
    nav.frame.loc[1, "lon"] = np.nan
    nav.frame.loc[2, "lon"] = 180.0  # [-180, 180)
    nav.frame.loc[3, "lat"] = 90.5
    flags, _ = flagged(nav)
    assert all(flags[i] & Flag.POSITION for i in (1, 2, 3))


@pytest.mark.parametrize(
    "column, value",
    [("quality", 0), ("quality", 6), ("nsat", 3), ("nsat", 51), ("hdop", 0.0), ("hdop", 51.0)],
)
def test_bad_gga_fix(column, value):
    nav = track(5)
    nav.frame.loc[2, column] = value
    flags, _ = flagged(nav)
    assert set(flags) == {2} and flags[2] & Flag.BAD_FIX


def test_fix_validity_needs_quality_and_satellites():
    """RMC-only formats report neither: nothing to judge"""
    nav = track(
        5,
        quality=pd.array([None] * 5, dtype="Int64"),
        nsat=pd.array([None] * 5, dtype="Int64"),
        hdop=np.full(5, np.nan),
    )
    assert flagged(nav)[0] == {}


def test_altitude_when_reported():
    nav = track(5)
    nav.frame.loc[1, "alt"] = -500.0
    nav.frame.loc[3, "alt"] = np.nan
    flags, _ = flagged(nav)
    assert set(flags) == {1} and flags[1] & Flag.ALTITUDE


def test_time_sequence_duplicates_backwards_and_big_jumps():
    nav = track(8)
    times = nav.frame["time"].copy()
    times[2] = times[1]  # duplicate
    times[4] = times[1] - pd.Timedelta(seconds=5)  # backwards
    times[6] = times[5] + pd.Timedelta(days=31)  # too far ahead
    nav.frame["time"] = times
    flags, _ = flagged(nav)
    assert {i for i, f in flags.items() if f & Flag.TIME_SEQUENCE} == {2, 4, 6}
    # the clock isn't advanced by the jump, so the next fix is still in sequence
    assert 7 not in flags or not flags[7] & Flag.TIME_SEQUENCE


def test_stale_repeat_while_moving_but_not_at_rest():
    nav = track(30)
    nav.frame.loc[20, ["lon", "lat"]] = nav.frame.loc[19, ["lon", "lat"]].to_numpy()
    flags, _ = flagged(nav)
    assert flags.get(20, 0) & Flag.STALE_REPEAT
    at_rest = track(30, speed=0.0)  # every position repeats: the median speed is 0
    assert not any(f & Flag.STALE_REPEAT for f in flagged(at_rest)[0].values())


def test_a_position_jump_flags_the_fix_itself():
    nav = track(60, step=0.5)
    nav.frame.loc[30, "lat"] += 4 * METRE  # 4 m off for one fix
    flags, _ = flagged(nav)
    assert set(flags) == {30} and flags[30] == Flag.ACCELERATION


def test_a_sideways_jump_is_caught():
    """Speed magnitudes barely change when a fix jumps across the track; velocity vectors do"""
    nav = track(60, step=0.5)
    nav.frame.loc[30, "lon"] += 4 * METRE / np.cos(np.radians(32.0))  # 4 m east, ship heads north
    flags, _ = flagged(nav)
    assert set(flags) == {30}


def test_a_step_in_the_track_is_reported_not_flagged():
    """A correction shifting every later fix 6 m east (~1.5 m s-2 over 2 s): no single fix is
    wrong, so none is flagged
    """
    nav = track(60, step=0.5)
    nav.frame.loc[30:, "lon"] += 6 * METRE / np.cos(np.radians(32.0))
    flags, result = flagged(nav)
    assert flags == {}
    assert "1 position step(s)" in result.notes[0]


def test_time_base_ignores_gnss_noise_at_high_rates():
    """±15 cm of noise at 2 Hz: every pair would exceed 1 m s-2 somewhere, 2 s doesn't"""
    rng = np.random.default_rng(1)
    nav = track(600, step=0.5)
    nav.frame["lat"] += rng.normal(0, 0.15, 600) * METRE
    assert flagged(nav)[0] == {}
    every_pair = Thresholds(accel_time_base=0.5)
    assert flagged(nav, thresholds=every_pair)[0] != {}


def test_acceleration_iterates_until_nothing_new_is_flagged():
    nav = track(80, step=1.0)
    for i, offset in [(20, 6), (21, 12), (22, 6)]:  # a 3-fix excursion
        nav.frame.loc[i, "lat"] += offset * METRE
    flags, _ = flagged(nav)
    assert {20, 21, 22} <= set(flags) <= {19, 20, 21, 22, 23}


def test_coarse_coordinates_raise_the_limit_and_say_so():
    nav = track(60, step=1.0, decimals=4)  # 1e-4° ≈ 11 m
    nav.frame["lat"] = nav.frame["lat"].round(4)
    flags, result = flagged(nav)
    assert result.accel_limit > 1.0 and "raised" in result.notes[0]
    assert flags == {}


# -- Comparison with R2R --


def _read(cruise, fileset, **window):
    directory = DATA / f"{cruise}_{fileset}_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset {cruise} {fileset}"
        )
    return rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29", **window)


def test_sp2613_has_no_flags_like_r2r():
    nav = _read(
        "SP2613",
        169851,
        start=datetime(2026, 7, 9, tzinfo=UTC),
        end=datetime(2026, 7, 10, tzinfo=UTC),
    )
    assert not qc.flag(nav).flags.any()


def test_sp2424_flags_short_excursions_that_r2r_misses():
    """R2R flags nothing (NavManager's acceleration check barely runs). We flag 33 fixes in six
    excursions of 2-4 s, each 1-2 m off the track and back again: sideways swings no ship can
    make (two of them at 3.6 m/s, swinging ~30° and back in about two seconds). Three steps,
    where the track shifts up to 3.5 m and stays (including a 3.7 m jump at the dock at
    16:01:55.50Z), are reported, not flagged.
    """
    nav = _read("SP2424", 161900)
    result = qc.flag(nav)
    flagged = np.flatnonzero(result.flags)
    assert len(flagged) == result.count(Flag.ACCELERATION) == 33
    seconds = (nav.frame["time"].iloc[flagged] - nav.frame["time"].iloc[0]).dt.total_seconds()
    clusters = np.split(seconds.to_numpy(), np.flatnonzero(np.diff(seconds.to_numpy()) > 2) + 1)
    assert len(clusters) == 6
    assert all(c[-1] - c[0] <= 4 for c in clusters)
    dock_jump = pd.Timestamp("2024-11-03T16:01:55.50Z")
    assert dock_jump not in nav.frame["time"].iloc[flagged].tolist()
    assert len(result.notes) == 1
    assert result.notes[0].startswith("3 position step(s) of up to 3.5 m")
