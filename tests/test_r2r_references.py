"""References whose R2R reports rate tests Y or R (#26): our results and ratings against R2R's,
with each difference explained in docs/navmanager-differences.md (R5, R6, R7, A3, A4).

| Fileset | R2R | Here |
|---|---|---|
| RR2406 / 161075 | R: C Y, gap R, S R, V R, A R | Y: gap Y. R2R's failures come from sentences its logger split across lines (R5) |
| RR2212 / 151339 | R: S R (50.08 %) | G. R2R read the files out of order (R7) |
| SP2220 / 150139 | R: C R, gap Y, S R, Q R, V R, A R | R: C R, gap R, Q R. The receiver had no fix most of the cruise |

And FKt230417 / 157454 (Falkor (too) POS/MV, nav33), rated G by both: the first nav33
reference, whose missing fixes in R2R's products are each explained (R1, R8, R9, Q8).
"""

import functools
import os

import numpy as np
import pandas as pd
import pytest
from conftest import CONFIGS, DATA

from nav_qa_processing import geocsv, maps, pdf_report, stats
from nav_qa_processing.integrations.r2r.config import load_cruise_config
from nav_qa_processing.integrations.r2r.reports import run_r2r
from nav_qa_processing.integrations.r2r.xml_report import R2R_NAMESPACE, build_qa_xml

# Every reference run uses the map data when it's cached (each fileset's run is cached for the
# session, so the same for all); the over-water results are checked only then (#110)
pytestmark = pytest.mark.map_data

TESTS = [
    "PercentCompleteness",
    "LongestGapEpoch",
    "PercentRecordsOutOfSequence",
    "PercentRecordsWithBadQualityIndicator",
    "PercentUnreasonableSpeeds",
    "PercentUnreasonableAccelerations",
]


def directory(name):
    path = DATA / f"{name}_gnss"
    if not (path / "r2r_qa.2.0.xml").exists():
        cruise, fileset = name.split("_")
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
            f"{cruise} {fileset}"
        )
    return path


def ours(name):
    """The fileset's R2R run, cached for the session per map-data state: other modules reuse it
    without map data, which a `map_data` test mustn't get (#110)
    """
    return _ours(name, os.environ.get(maps.NO_MAP_DATA, ""))


@functools.cache
def _ours(name, no_map_data):
    return run_r2r(directory(name), CONFIGS / f"{name}.toml")


def r2rs(name):
    """R2R's ratings and results, by test"""
    import xml.etree.ElementTree as ET

    from nav_qa_processing.integrations.r2r.xml_report import R2R_NAMESPACE

    ns = {"r2r": R2R_NAMESPACE}
    root = ET.parse(directory(name) / "r2r_qa.2.0.xml").getroot()
    tests = {
        t.get("name"): (
            t.findtext("r2r:rating", "", ns),
            float(t.findtext("r2r:test_result", "", ns)),
        )
        for t in root.findall("r2r:certificate/r2r:tests/r2r:test", ns)
    }
    return root.findtext("r2r:certificate/r2r:rating", "", ns), tests


def ratings(run):
    return run.r2r.rating, {t.name: (t.rating, t.result) for t in run.r2r.tests}


def test_rr2406_split_sentences():
    """R2R: R on five tests, from absurd values (653,911 % out of sequence, 14 million m s-1):
    NavManager reads the first part of each sentence its logger split across lines as a whole
    sentence. Rejoined, the cruise is complete but for one 47-minute gap (R5)
    """
    run = ours("RR2406_161075")
    rating, tests = ratings(run)
    assert rating == "Y"
    assert {name: r for name, (r, _) in tests.items()} == dict.fromkeys(TESTS, "G") | {
        "LongestGapEpoch": "Y"
    }
    assert tests["PercentCompleteness"][1] == 99.07 and tests["LongestGapEpoch"][1] == 2857.0
    r2r_rating, r2r_tests = r2rs("RR2406_161075")
    assert r2r_rating == "R"
    assert [r2r_tests[t][0] for t in TESTS] == ["Y", "R", "R", "G", "R", "R"]
    assert r2r_tests["PercentRecordsOutOfSequence"][1] == 653911.11  # not a percentage
    # R2R's bestres: 266,841 records, 191,414 flagged: truncated GGAs with their missing fields
    # as 0, some of them without their W (at 83°E), and good fixes near them
    best = geocsv.read(directory("RR2406_161075") / "products/data/RR2406_bestres.geoCSV")
    assert (len(best.records), int(best.records["flagged"].sum())) == (266_841, 191_414)
    flagged = best.records[best.records["flagged"]]
    assert int((flagged["nmea_quality"] == 0).sum()) == 120_694
    assert int((flagged["ship_longitude"] > 0).sum()) == 45_243
    # One GGA cut after the geoid separation, before a 4-minute break in the logger's stream, has
    # every field we read: kept (#81)
    assert len(run.result.nav.frame) == 385_321 and (run.result.nav.frame["lon"] < 0).all()


def test_rr2212_files_out_of_order():
    """R2R: R on out of sequence (50.08 %). R2R's bestres runs Nov 5, 4, 3, 6, 7, so Nov 3 and 4
    come after Nov 5, all flagged out of sequence; navdatalist sorts by each file's first ZDA,
    which would order them correctly (R7). Its catalog extent leaves out those two days' track
    """
    run = ours("RR2212_151339")
    rating, tests = ratings(run)
    assert rating == "G" and tests["PercentRecordsOutOfSequence"] == ("G", 0.0)
    r2r_rating, r2r_tests = r2rs("RR2212_151339")
    assert r2r_rating == "R" and r2r_tests["PercentRecordsOutOfSequence"] == ("R", 50.08)
    best = geocsv.read(directory("RR2212_151339") / "products/data/RR2212_bestres.geoCSV")
    days = best.records["iso_time"].dt.day.to_numpy()
    starts = [int(days[i]) for i in np.flatnonzero(np.diff(days) != 0) + 1]
    assert starts[:4] == [5, 3, 4, 2]  # Nov 3 and 4 after Nov 5 (and one misdated record)
    # the transit from Moss Landing, flagged by R2R, is outside R2R's extent
    extent = load_cruise_config(CONFIGS / "RR2212_151339.toml").extent  # R2R's catalog
    assert run.result.nav.frame["lat"].max() > extent.northernmost + 2
    # Over water (#79): Oakland's inner harbor is land at 1:10m, up to 1.44 km in; within the
    # tolerance
    over_water = next(c for c in run.result.stats.checks if c.name == "Over water")
    if run.result.stats.inland is not None:
        assert over_water.status == "pass" and "374 on land within 3000 m" in over_water.detail
        assert round(np.nanmax(run.result.stats.inland)) == 1438
    else:
        assert over_water.status == "n/a"


def test_sp2220_no_fix():
    """The receiver logged empty GGAs (quality 0, no position) for most of the cruise. Both rate
    R; R2R's completeness (33.99 %) counts records without a position, ours only fixes
    """
    run = ours("SP2220_150139")
    rating, tests = ratings(run)
    assert rating == "R"
    assert tests["PercentCompleteness"] == ("R", 6.02)
    assert tests["LongestGapEpoch"][0] == "R"
    assert tests["PercentRecordsWithBadQualityIndicator"] == ("R", 82.61)
    r2r_rating, r2r_tests = r2rs("SP2220_150139")
    assert r2r_rating == "R"
    assert r2r_tests["PercentRecordsWithBadQualityIndicator"][0] == "R"
    assert r2r_tests["PercentCompleteness"] == ("R", 33.99)
    # The checks in a line (#68)
    status, line = run.result.stats.verdict
    no_map_data = " n/a: Over water." if run.result.stats.inland is None else ""
    assert status == "warn" and line == (
        "4 of 8 checks warn: Completeness 6.02 %; Longest gap 28:21:43.500 (102103.500 s); Bad "
        "quality indicator 82.61 %; Within the expected area 3 outside." + no_map_data
    )
    # What the reader skipped (#67): every GGA without a time and ZDA without a date, and
    # the 09-27 file it couldn't read
    by_file = stats.counts_by_file(run.result.nav, run.result.stats).set_index("file")
    assert by_file.loc["Total", stats.SKIPPED_SENTENCES] == 695_387 + 349_970
    assert by_file.loc["abx-two-rx-1_20220927000000.raw", "status"] == (
        "not read: no ZDA sentence"
    )
    # Over the fixes that pass QC (#66), the receiver's junk is gone
    s = run.result.stats
    assert (s.hdop.maximum, s.good.hdop.maximum) == (999.9, 47.2)
    assert s.speed.maximum > 6000 and s.good.speed.maximum < 70
    assert s.good.records == 58_307
    # The gaps (#65): 59, the longest 28.4 h, from 09-22 into 09-23
    found = stats.gaps(run.result.nav, run.result.qc, run.result.config.thresholds)
    assert len(found) == run.result.stats.gaps == 59
    longest = found.iloc[0]
    assert longest["length"] == run.result.stats.longest_gap == 102_103.5
    assert (longest["file_before"], longest["file_after"]) == (
        "abx-two-rx-1_20220922000000.raw",
        "abx-two-rx-1_20220923000000.raw",
    )
    assert found["length"].is_monotonic_decreasing
    # Shown as hh:mm:ss in the PDF's statistics and gaps table (#73); R2R's XML keeps seconds
    lay = pdf_report.layout()
    cells = lambda table: [getattr(c, "text", c) for row in table._cellvalues for c in row]
    assert "28:21:43.500" in cells(pdf_report._statistics(lay, run.result))
    assert "28:21:43.500" in cells(pdf_report._gaps(lay, run.result)[-1])
    xml = build_qa_xml(run.result, run.r2r).getroot()
    epoch = next(
        t for t in xml.iter(f"{{{R2R_NAMESPACE}}}test") if t.get("name") == "LongestGapEpoch"
    )
    assert epoch.findtext(f"{{{R2R_NAMESPACE}}}test_result") == "102103.500"


def test_fkt230417_nav33():
    """Falkor (too)'s Applanix POS/MV, logged as ISO timestamp + GGA (nav33). Both rate G. R2R's
    bestres has every fix but the GGAs with checksum 0A-0F (R1: 48,427), every 86,400th (R8:
    18) and the last (R9), so its completeness is 96.94 %, ours 99.86 %. QC flags the 4.7 min
    the POS/MV spent 650 m off the track (Q8: 283 fixes); R2R flags none of them
    """
    from nav_qa_processing import rawnav
    from nav_qa_processing.qc import Flag

    run = ours("FKt230417_157454")
    rating, tests = ratings(run)
    assert rating == "G" and {r for r, _ in tests.values()} == {"G"}
    assert tests["PercentCompleteness"][1] == 99.86
    r2r_rating, r2r_tests = r2rs("FKt230417_157454")
    assert r2r_rating == "G" and r2r_tests["PercentCompleteness"] == ("G", 96.94)
    assert tests["LongestGapEpoch"][1] == r2r_tests["LongestGapEpoch"][1] == 760.021

    nav = run.result.nav.frame
    best = geocsv.read(directory("FKt230417_157454") / "products/data/FKt230417_bestres.geoCSV")
    theirs = best.records["iso_time"].dt.as_unit("ns").astype("int64").to_numpy()
    times = nav["time"].dt.as_unit("ns").astype("int64").to_numpy()
    assert np.isin(theirs, times).all()  # every record of R2R's is ours
    missing = ~np.isin(times, theirs)
    files = {p.name: p for p in (directory("FKt230417_157454") / "data").iterdir()}
    lines = {name: rawnav.read_lines(path) for name, path in files.items()}
    given = np.array(
        [
            lines[f][n - 1].rsplit("*", 1)[1][:2].upper()
            for f, n in zip(nav["source_file"], nav["line"])
        ]
    )
    hex_letters = np.isin(given, ["0A", "0B", "0C", "0D", "0E", "0F"])
    assert int(hex_letters.sum()) == int((hex_letters & missing).sum()) == 48_427  # R1
    others = np.flatnonzero(missing & ~hex_letters)
    assert others[-1] == len(nav) - 1  # R9: the last fix
    kept_before = np.cumsum(~missing)[others[:-1]]
    assert len(others) - 1 == 18 and (kept_before % 86_399 == 0).all()  # R8
    unreachable = run.result.qc.flags & int(Flag.UNREACHABLE) != 0
    assert int(unreachable.sum()) == 283  # Q8: R2R has those it read, none flagged
    assert not (missing & unreachable & ~hex_letters).any()
    off = np.isin(theirs, times[unreachable])
    assert off.sum() == (unreachable & ~missing).sum() and not best.records["flagged"][off].any()
    # Checks by file (#61): 20 files whose counts add up to the checks'; the unreachable
    # stretch is in the 04-27 file
    s = run.result.stats
    by_file = stats.counts_by_file(run.result.nav, s).set_index("file")
    total = by_file.loc["Total"]
    assert len(by_file) == 21 and (by_file["status"].iloc[:-1] == "read").all()
    # 285 as before (283 of them unreachable, Q8), and 8 quality-0 GGAs without satellites or
    # HDOP (Q9, #92)
    assert by_file.iloc[:-1]["Flagged by QC"].sum() == total["Flagged by QC"] == 293
    assert (total["Unreasonable speeds"], total["Unreasonable accelerations"]) == (
        s.unreasonable_speeds,
        s.unreasonable_accelerations,
    )
    assert (total["Bad quality"], total["Gaps"]) == (s.bad_quality, s.gaps) == (8, 2)
    assert by_file.loc["FKt230417_posmv_gga-2023-04-27.txt", "Flagged by QC"] == 283
    # Over water (#79): alongside in San Juan, which is land at 1:10m, for 30 minutes
    over_water = next(c for c in s.checks if c.name == "Over water")
    if s.inland is not None:
        assert over_water.status == "pass" and "1835 on land within 3000 m" in over_water.detail
    else:
        assert over_water.status == "n/a"


def _times(frame_times) -> np.ndarray:
    return frame_times.dt.as_unit("ns").astype("int64").to_numpy()


def test_en735_nav2():
    """Endeavor's NOAA SCS (nav02: the logger's date and time, then GGA). Both rate G. R2R's
    bestres is ours less 9 records: every 86,400th GGA (R8: 3), the last (R9) and the earlier of
    5 repeated times (NavManager keeps the later; QC flags it here). Their values are equal
    """
    run = ours("EN735_165419")
    rating, tests = ratings(run)
    r2r_rating, r2r_tests = r2rs("EN735_165419")
    assert rating == r2r_rating == "G"
    for name in TESTS[:4]:  # the tests not from speeds and accelerations (A5)
        assert tests[name] == r2r_tests[name]
    nav = run.result.nav.frame
    best = geocsv.read(directory("EN735_165419") / "products/data/EN735_bestres.geoCSV").records
    times, theirs = _times(nav["time"]), _times(best["iso_time"])
    assert (len(times), len(theirs)) == (300_036, 300_027)
    assert np.isin(theirs, times).all()
    repeated = nav["time"].duplicated(keep="last").to_numpy()
    assert int(repeated.sum()) == 5
    missing = nav["time"][~np.isin(times, theirs)].dt.strftime("%m-%dT%H:%M:%S").tolist()
    assert missing == ["08-20T12:39:20", "08-21T12:39:21", "08-22T12:39:22", "08-22T23:59:58"]
    kept = nav[~repeated & np.isin(times, theirs)]
    assert np.allclose(kept["lon"], best["ship_longitude"], atol=5e-7)
    assert (kept["alt"].to_numpy() == best["antenna_height"].to_numpy()).all()


def test_skq202516s_nav24():
    """Sikuliaq's Seapath 380+ (nav24: logger ID, ISO timestamp, NMEA; R2R format 100103). Both
    rate G with the same results, and flag the same 62 records. R2R's bestres is ours less the
    final fix, after the last ZDA (R2)
    """
    run = ours("SKQ202516S_165743")
    rating, tests = ratings(run)
    r2r_rating, r2r_tests = r2rs("SKQ202516S_165743")
    assert rating == r2r_rating == "G" and tests == r2r_tests
    nav = run.result.nav.frame
    best = geocsv.read(directory("SKQ202516S_165743") / "products/data/SKQ202516S_bestres.geoCSV")
    records = best.records
    times, theirs = _times(nav["time"]), _times(records["iso_time"])
    assert len(times) == len(theirs) + 1 == 771_064
    assert (times[:-1] == theirs).all()
    assert nav["time"].iloc[-1] == pd.Timestamp("2025-09-07T22:11:03.97Z")
    assert np.allclose(nav["lat"].iloc[:-1], records["ship_latitude"], atol=5e-9)
    flagged = run.result.qc.flags[:-1] != 0
    assert int(flagged.sum()) == int(records["flagged"].sum()) == 62


def test_sr2612_five_hz():
    """Sally Ride's Seapath 380+ at an unbroken 5 Hz (nav29). R2R's bestres is ours less the
    final fix (R2), with the same 960 flags. Completeness is 100 % here, 80.05 % in R2R's report,
    so R2R rates it Y and we G (A8, #108)
    """
    run = ours("SR2612_169918")
    rating, tests = ratings(run)
    r2r_rating, r2r_tests = r2rs("SR2612_169918")
    assert (rating, r2r_rating) == ("G", "Y")
    assert tests["PercentCompleteness"] == ("G", 100.0)
    assert r2r_tests["PercentCompleteness"] == ("Y", 80.05)
    for name in TESTS[1:5]:  # the others, but accelerations (A5)
        assert tests[name] == r2r_tests[name]
    nav = run.result.nav.frame
    best = geocsv.read(directory("SR2612_169918") / "products/data/SR2612_bestres.geoCSV").records
    times, theirs = _times(nav["time"]), _times(best["iso_time"])
    assert len(times) == len(theirs) + 1 == 880_236 and (times[:-1] == theirs).all()
    assert int((run.result.qc.flags[:-1] != 0).sum()) == int(best["flagged"].sum()) == 960
