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

import numpy as np
import pytest
from conftest import CONFIGS, DATA

from nav_qa_processing import geocsv, stats
from nav_qa_processing.integrations.r2r.config import load_cruise_config
from nav_qa_processing.integrations.r2r.reports import run_r2r

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


@functools.cache
def ours(name):
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
    assert len(run.result.nav.frame) == 385_320 and (run.result.nav.frame["lon"] < 0).all()


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
    assert by_file.iloc[:-1]["Flagged by QC"].sum() == total["Flagged by QC"] == 285
    assert (total["Unreasonable speeds"], total["Unreasonable accelerations"]) == (
        s.unreasonable_speeds,
        s.unreasonable_accelerations,
    )
    assert (total["Bad quality"], total["Gaps"]) == (s.bad_quality, s.gaps) == (8, 2)
    assert by_file.loc["FKt230417_posmv_gga-2023-04-27.txt", "Flagged by QC"] == 283
