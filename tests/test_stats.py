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


def test_good_fix_ranges_leave_out_flagged_fixes():
    """The same ranges over the fixes that pass QC (#66): a fix 40 m off and an invalid one
    with HDOP 99 show in the pre-QC ranges only
    """
    lat = 32.0 + np.arange(60) * 3.0 * METRE
    lat[30] += 40 * METRE
    quality = [2] * 60
    quality[10] = 0
    nav = nav_from(np.arange(60), lat=lat, quality=quality)
    nav.frame.loc[10, "hdop"] = 99.0
    s = compute(nav)
    assert s.hdop.maximum == 99.0 and s.good.hdop.maximum == 0.8
    assert s.speed.maximum > 8.7 and s.good.speed.maximum < 8.7
    assert s.good.records == int((qc.flag(nav, Thresholds()).flags == 0).sum()) < s.records
    assert s.good.first == START.to_pydatetime() and s.good.acceleration.maximum < 1
    # Without flags, the two are the same
    clean = compute(nav_from(range(10)))
    assert (clean.good.speed, clean.good.hdop, clean.good.records) == (
        clean.speed,
        clean.hdop,
        clean.records,
    )


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


def two_files():
    """a.txt: a fix 40 m off (speeds and accelerations) and a no-fix record; b.txt, after a
    gap that crosses from a.txt: a record out of sequence and a no-fix record; c.txt skipped
    """
    seconds = [*range(60), *range(400, 411), 409.5, *range(411, 460)]
    lat = 32.0 + np.asarray(seconds, dtype=float) * 3.0 * METRE
    lat[30] += 40 * METRE
    quality = [2] * len(seconds)
    quality[5] = quality[65] = 0
    nav = nav_from(seconds, lat=lat, quality=quality)
    nav.frame["source_file"] = pd.Categorical(["a.txt"] * 60 + ["b.txt"] * 61)
    nav.files, nav.skipped_files = ["a.txt", "b.txt"], ["c.txt"]
    return nav


def test_counts_by_file():
    nav = two_files()
    s = compute(nav)
    table = stats.counts_by_file(nav, s).set_index("file")
    a, b, total = table.loc["a.txt"], table.loc["b.txt"], table.loc["Total"]
    assert (a["fixes"], b["fixes"], total["fixes"]) == (60, 61, 121)
    assert (a["Unreasonable speeds"], b["Unreasonable speeds"]) == (2, 0)
    assert a["Unreasonable accelerations"] >= 1 and b["Unreasonable accelerations"] == 0
    assert (a["Gaps"], b["Gaps"]) == (0, 1)  # the gap ends in b.txt
    assert (a["Out of sequence"], b["Out of sequence"]) == (0, 1)
    assert (a["Bad quality"], b["Bad quality"]) == (1, 1)
    # The files' counts add up to the total, which is what the checks counted
    columns = ["fixes", *stats.COUNTED.values()]
    columns.remove("Outside area")
    assert (table.loc[["a.txt", "b.txt"], columns].sum() == total[columns]).all()
    assert total["Unreasonable speeds"] == s.unreasonable_speeds
    assert total["Unreasonable accelerations"] == s.unreasonable_accelerations
    assert (total["Gaps"], total["Out of sequence"]) == (s.gaps, s.out_of_sequence)
    assert total["Flagged by QC"] == int((qc.flag(nav, Thresholds()).flags != 0).sum())
    # Without an expected area there's no outside-area count; a skipped file has no counts
    assert table["Outside area"].isna().all()
    assert table.loc["c.txt", "status"] == stats.SKIPPED and pd.isna(table.loc["c.txt", "fixes"])


def test_counts_by_file_show_skipped_sentences_and_unread_files():
    nav = two_files()
    nav.skipped_lines = {"a.txt": {"GGA without a time": 7, "failed its checksum": 2}}
    nav.unread_files = {"d.txt": "no ZDA sentence"}
    table = stats.counts_by_file(nav, compute(nav)).set_index("file")
    skipped = table[stats.SKIPPED_SENTENCES]
    assert (skipped["a.txt"], skipped["b.txt"], skipped["Total"]) == (9, 0, 9)
    assert table.loc["d.txt", "status"] == "not read: no ZDA sentence"
    assert list(table.index) == ["a.txt", "b.txt", "d.txt", "c.txt", "Total"]
    reasons = stats.skipped_by_file(nav)
    assert reasons.to_dict("records") == [
        {"file": "a.txt", "reason": "GGA without a time", "sentences": 7},
        {"file": "a.txt", "reason": "failed its checksum", "sentences": 2},
    ]


def test_gaps_longest_first_with_their_files():
    nav = two_files()  # a.txt ends at 59 s; b.txt starts at 400 s
    table = stats.gaps(nav, qc.flag(nav, Thresholds()), Thresholds())
    assert len(table) == 1 == compute(nav).gaps
    gap = table.iloc[0]
    assert gap["length"] == 341.0 and (gap["file_before"], gap["file_after"]) == ("a.txt", "b.txt")
    assert gap["start"] == START + pd.Timedelta(seconds=59)
    assert gap["end"] == START + pd.Timedelta(seconds=400)
    # Several: the longest first, ties in time order
    nav = nav_from([0, 1, 400, 401, 1100, 1101, 1800])
    lengths = stats.gaps(nav, qc.flag(nav, Thresholds()), Thresholds())["length"].tolist()
    assert lengths == [699.0, 699.0, 399.0]
    starts = stats.gaps(nav, qc.flag(nav, Thresholds()), Thresholds())["start"].tolist()
    assert starts[0] < starts[1]
    assert stats.gaps(nav_from(range(10)), qc.flag(nav_from(range(10))), Thresholds()).empty


def test_counts_by_file_outside_the_expected_area():
    nav = two_files()
    s = compute(nav, extent=Extent(-118.0, -116.0, 32.0, 32.0 + 59 * 3.0 * METRE))
    table = stats.counts_by_file(nav, s).set_index("file")
    assert (table.loc["a.txt", "Outside area"], table.loc["b.txt", "Outside area"]) == (0, 61)
    check = {c.name: c for c in s.checks}["Within the expected area"]
    assert check.value == f"{table.loc['Total', 'Outside area']} outside"


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["pass", "pass", "info"], ("pass", "All 2 checks pass.")),
        (["pass", "n/a", "info"], ("pass", "1 of 2 checks passes. n/a: C1.")),
        (["warn", "pass", "info"], ("warn", "1 of 2 checks warns: C0 v0.")),
        (["warn", "warn", "n/a"], ("warn", "2 of 3 checks warn: C0 v0; C1 v1. n/a: C2.")),
        (["n/a", "info"], ("n/a", "No check could be made. n/a: C0.")),
    ],
)
def test_verdict(statuses, expected):
    s = compute(nav_from(range(5)))
    s.checks = [stats.CheckResult(f"C{i}", st, f"v{i}") for i, st in enumerate(statuses)]
    assert s.verdict == expected


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
