import re
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import DATA

from nav_qa_processing import qc, rawnav, stats
from nav_qa_processing.config import Extent, Thresholds

START = pd.Timestamp("2026-07-09T12:00:00Z")
METRE = 1 / 111_320  # [degree] of latitude


def nav_from(seconds, lat=None, quality=None, n_sat=12):
    seconds = np.asarray(seconds, dtype=float)
    n = len(seconds)
    frame = pd.DataFrame(
        {
            "time": START + pd.to_timedelta(seconds, unit="s"),
            "lon": np.full(n, -117.0),
            "lat": 32.0 + seconds * 3.0 * METRE if lat is None else lat,
            "quality": pd.array([2] * n if quality is None else quality, dtype="Int64"),
            "nsat": pd.array([n_sat] * n, dtype="Int64"),
            "hdop": np.full(n, 0.8),
            "alt": np.full(n, 7.0),
        }
    )
    return rawnav.RawNav(frame, {"time": 1, "lon": 10, "lat": 10, "hdop": 1, "alt": 1}, ["x"])


def compute(nav, thresholds=None, extent=None):
    thresholds = thresholds or Thresholds()
    return stats.compute(nav, qc.flag(nav, thresholds), thresholds, extent)


def test_modal_interval_ties_go_to_the_shorter_step():
    assert stats.modal_interval(np.array([1.0, 1.0, 0.5, 0.5, 2.0])) == 0.5
    assert stats.modal_interval(np.array([1.0004, 0.9996, 1.0])) == 1.0  # to the millisecond
    assert stats.modal_interval(np.array([])) is None


def test_completeness_counts_interval_slots():
    s = compute(nav_from([0, 1, 2, 3, 7, 8, 9]))  # 3 missing seconds out of 10
    assert s.modal_interval == 1.0 and (s.occupied_epochs, s.possible_epochs) == (7, 10)
    assert s.completeness == pytest.approx(70.0)
    assert s.longest_gap == 4.0 and s.gaps == 0


def test_completeness_never_exceeds_100_percent():
    # the modal step is 1 s; the extra record at 1.5 s fills no new slot
    s = compute(nav_from([0, 1, 1.5, 2, 3, 4, 5]))
    assert s.modal_interval == 1.0 and s.completeness == 100.0


def test_gaps_of_max_gap_or_longer():
    s = compute(nav_from([0, 1, 301, 302, 1000]))
    assert s.gaps == 2 and s.longest_gap == 698.0


def test_out_of_sequence_records():
    s = compute(nav_from([0, 1, 2, 1.5, 3, 4]))
    assert s.out_of_sequence == 1 and s.out_of_sequence_percent == pytest.approx(100 / 6)


def test_bad_quality_uses_qcs_valid_range():
    """Quality 3 (PPS) is a valid fix; navqa counts it as bad"""
    s = compute(nav_from(range(10), quality=[2, 3, 1, 0, 6, 2, 2, 4, 5, 2]))
    assert s.bad_quality == 2 and s.bad_quality_percent == pytest.approx(20.0)


def test_no_quality_indicator_is_not_applicable():
    s = compute(nav_from(range(5), quality=[None] * 5))
    assert s.bad_quality_percent is None
    check = next(c for c in s.checks if c.name == "Bad quality indicator")
    assert check.status == "n/a"


def test_unreasonable_speeds_and_accelerations():
    lat = 32.0 + np.arange(60) * 3.0 * METRE
    lat[30] += 40 * METRE  # 40 m off for one fix
    s = compute(nav_from(np.arange(60), lat=lat))
    assert s.unreasonable_speeds == 2 and s.speed.maximum > 8.7
    assert s.unreasonable_accelerations >= 1 and s.acceleration.maximum > 1


def test_ranges():
    s = compute(nav_from(range(5)))
    assert (s.nsv.minimum, s.nsv.maximum) == (12, 12) and s.altitude.maximum == 7.0


def test_extent_check():
    nav = nav_from(range(5))
    inside = Extent(-118.0, -116.0, 31.0, 33.0)
    outside = Extent(10.0, 11.0, 0.0, 1.0)
    check = {c.name: c for c in compute(nav, extent=inside).checks}["Within the expected area"]
    assert check.status == "pass"
    check = {c.name: c for c in compute(nav, extent=outside).checks}["Within the expected area"]
    assert check.status == "warn" and check.value == "5 outside"
    check = {c.name: c for c in compute(nav).checks}["Within the expected area"]
    assert check.status == "n/a"


def test_core_checks_use_thresholds():
    s = compute(nav_from([0, 1, 2, 3, 7, 8, 9]), Thresholds(min_completeness=60.0))
    assert {c.name: c.status for c in s.checks}["Completeness"] == "pass"
    s = compute(nav_from([0, 1, 2, 3, 7, 8, 9]))
    assert {c.name: c.status for c in s.checks}["Completeness"] == "warn"
    assert "WARN  Completeness: 70.00 %" in str(s)


# -- Against R2R's QA 2.0 reports --


def r2r_report(path):
    """Test results and ranges from R2R's QA 2.0 XML, as numbers"""
    text = path.read_text()
    results = {
        name: float(re.search(r"test_result[^>]*>([^<]*)", body).group(1))
        for name, body in re.findall(
            r'<r2r:test name="(\w+)".*?>(.*?)</r2r:test>', text, re.DOTALL
        )
    }
    ranges = {
        name: tuple(float(v) for _, v in re.findall(r'range name="(\w+)"[^>]*>([^<]*)', body))
        for name, body in re.findall(
            r'filesetinfo_supplemental name="(\w+)">(.*?)</r2r:filesetinfo_supplemental>',
            text,
            re.DOTALL,
        )
    }
    return results, ranges


def ours(cruise, fileset, start=None, end=None):
    directory = DATA / f"{cruise}_{fileset}_gnss"
    if not (directory / "r2r_qa.2.0.xml").exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset {cruise} {fileset}"
        )
    window = {"start": start, "end": end} if start else {}
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29", **window)
    s = stats.compute(nav, qc.flag(nav, start=start, end=end), Thresholds())
    return s, *r2r_report(directory / "r2r_qa.2.0.xml")


def assert_shared_values_equal(s, results, ranges):
    """What we compute the same way as navqa equals R2R's at R2R's precision"""
    assert round(s.completeness, 2) == results["PercentCompleteness"]
    assert round(s.longest_gap, 2) == results["LongestGapEpoch"]
    assert round(s.out_of_sequence_percent, 2) == results["PercentRecordsOutOfSequence"]
    assert round(s.bad_quality_percent, 2) == results["PercentRecordsWithBadQualityIndicator"]
    assert round(s.unreasonable_speeds_percent, 2) == results["PercentUnreasonableSpeeds"]
    assert (s.nsv.minimum, s.nsv.maximum) == ranges["NSV"]
    assert (s.hdop.minimum, s.hdop.maximum) == ranges["HDOP"]
    assert (s.altitude.minimum, s.altitude.maximum) == ranges["Altitude"]
    assert (round(s.speed.minimum, 2), round(s.speed.maximum, 2)) == ranges["HorizontalSpeed"]


def test_sp2613_matches_r2r():
    s, results, ranges = ours(
        "SP2613", 169851, datetime(2026, 7, 9, tzinfo=UTC), datetime(2026, 7, 10, tzinfo=UTC)
    )
    assert_shared_values_equal(s, results, ranges)
    assert (
        round(s.unreasonable_accelerations_percent, 2)
        == 0.0
        == (results["PercentUnreasonableAccelerations"])
    )
    # the acceleration range differs by definition: QC's over 2 s, not every 0.5 s pair
    assert ranges["HorizontalAcceleration"] == (-0.898, 0.872)
    assert -0.6 < s.acceleration.minimum < 0 < s.acceleration.maximum < 0.6


def test_sp2424_matches_r2r_but_for_accelerations():
    """R2R: 0.16 % unreasonable accelerations, -14.3 to 14.8 m s-2, from every pair of 0.5 s
    fixes (GNSS noise); ours: 0.01 %, about -0.9 to 1.0, over QC's 2 s time base
    """
    s, results, ranges = ours("SP2424", 161900)
    assert_shared_values_equal(s, results, ranges)
    assert results["PercentUnreasonableAccelerations"] == 0.16
    assert round(s.unreasonable_accelerations_percent, 2) == 0.01
    assert ranges["HorizontalAcceleration"] == (-14.257, 14.766)
    assert s.acceleration.maximum < 1.1


def test_sp2503_matches_r2r_on_completeness_and_the_long_gap():
    """R2R rates it R on completeness (47.34 %) and Y on its 48,625.5 s gap; ours agree"""
    s, results, ranges = ours(
        "SP2503", 167484, datetime(2025, 2, 13, tzinfo=UTC), datetime(2025, 2, 15, tzinfo=UTC)
    )
    assert_shared_values_equal(s, results, ranges)
    statuses = {c.name: c.status for c in s.checks}
    assert statuses["Completeness"] == statuses["Longest gap"] == "warn"
