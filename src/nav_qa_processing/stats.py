"""QA statistics on the pre-QC records, and the core checks (NavManager's navqa, done soundly).

`compute(nav, qc_result, thresholds, extent)` -> `NavStats`. The records used are those QC
doesn't reject outright (`DATE_RANGE`, `POSITION`, `NULL_ISLAND`: navqa's date and position
filter); out-of-sequence records are QC's `TIME_SEQUENCE`. The statistics:

- **Modal interval:** the most common step forward between in-sequence records, to the
  millisecond; a tie goes to the shorter step.
- **Completeness:** the share of interval slots, from the first in-sequence record to the last,
  that hold a record (at most 100 %).
- **Gaps:** the longest step between in-sequence records, and how many are `max_gap` or longer.
- **Out of sequence:** the share of records QC flags `TIME_SEQUENCE`.
- **Bad quality:** the share of records reporting a GGA quality outside QC's valid range
  (`min_quality` to `max_quality`, 1 to 5); none when no record reports one. Every record within
  the dates counts, with or without a position: a receiver without a fix reports quality 0 and
  no position (SP2220: 279,688 of 338,581 records).
- **Speeds:** between successive in-sequence records; unreasonable above `max_speed`.
- **Accelerations:** QC's acceleration at each fix (velocity vectors over `accel_time_base`);
  unreasonable above `max_accel`, so QA and QC agree. The range is the signed rate of change of
  speed over the same time base.
- **Ranges:** NSV, HDOP and altitude over the records reporting them.
- **Good fixes** (`NavStats.good`, #66): the same ranges, and the speeds and accelerations, over
  the fixes that pass QC alone: the usable track, beside navqa's pre-QC figures (SP2220's HDOP
  999.9 and 6,660 m s-1 are its receiver's junk, not its track's).

The core checks (pass/warn/info/n/a) compare these with `Thresholds`, whose defaults equal R2R's
green bounds; R2R's own G/Y/R ratings belong to the R2R integration.

- **Over water** (#79): with the land under the track (`land.Land`), each usable record's
  distance on land to the nearest water; over land (warn) beyond `max_inland`, and reported
  beyond `land_tolerance` (#95). Without it (no map data),
  the check is n/a.

`gaps` lists the gaps (#65): when each started and ended, how long it was and the files either
side, the longest first; the same steps the gap statistics count.

`NavStats.counted` keeps which records each test counted, and `counts_by_file` splits the counts
by nav file (#61). A test between two fixes (a speed, a step out of sequence, a gap) counts in
the later fix's file, also across a file boundary; an acceleration is at a fix.

Different from navqa (see docs/navmanager-differences.md, A2-A6): a count of interval slots
replaces its accumulated "countable epochs" (whose end-of-file loop compares milliseconds with
seconds); quality 3 (PPS) isn't bad; accelerations come from QC's definition rather than from
scalar speeds between every pair of records.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from nav_qa_processing import geodesy
from nav_qa_processing import quantities as q
from nav_qa_processing.config import Extent, Thresholds
from nav_qa_processing.qc import Flag, QCResult, acceleration_at_fixes
from nav_qa_processing.rawnav import RawNav
from nav_qa_processing.readable import Readable, block, counts, status_line

if TYPE_CHECKING:
    from nav_qa_processing.land import Land

UNUSABLE = Flag.DATE_RANGE | Flag.POSITION | Flag.NULL_ISLAND
# [degree] about 11 cm: R2R's catalog rounds its cruise extent (the track's own) to 6 to 10
# decimals, which can leave the fixes on its edges just outside it (SP2613: 3 by 3e-11°;
# RR2406: 1 by 1.7e-7°)
EXTENT_TOLERANCE = 1e-6


@dataclass
class CheckResult(Readable):
    name: str
    status: str  # pass, warn, fail, info or n/a
    value: str
    detail: str = ""

    def __str__(self) -> str:
        detail = f" ({self.detail})" if self.detail else ""
        return status_line(self.status, f"{self.name}: {self.value}{detail}")


@dataclass
class Range:
    minimum: float | None
    maximum: float | None


@dataclass
class GoodFixes:
    """The ranges over the fixes that pass QC (flags 0), speeds and accelerations between them"""

    records: int
    first: datetime | None
    last: datetime | None
    nsv: Range
    hdop: Range
    altitude: Range  # [m]
    speed: Range  # [m s-1]
    acceleration: Range  # [m s-2], signed rate of change of speed


@dataclass
class NavStats(Readable):
    records: int  # usable records
    first: datetime | None
    last: datetime | None
    modal_interval: float | None  # [s]
    possible_epochs: int
    occupied_epochs: int
    completeness: float | None  # [percent]
    longest_gap: float | None  # [s]
    gaps: int  # steps of max_gap or longer
    out_of_sequence: int
    out_of_sequence_percent: float | None
    quality_reported: int  # records reporting a quality indicator
    bad_quality: int
    bad_quality_percent: float | None  # None when no record reports a quality indicator
    speeds: int
    unreasonable_speeds: int
    unreasonable_speeds_percent: float | None
    accelerations: int
    unreasonable_accelerations: int
    unreasonable_accelerations_percent: float | None
    nsv: Range
    hdop: Range
    altitude: Range  # [m]
    speed: Range  # [m s-1]
    acceleration: Range  # [m s-2], signed rate of change of speed
    checks: list[CheckResult] = field(default_factory=list)
    good: GoodFixes | None = None
    # Per record of the frame: whether each test counted it, by COUNTED's keys. "outside_area"
    # only with an expected area, "over_land" only with the land
    counted: dict[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)
    # Per record: its distance inside the coastline [m], 0 at sea, NaN for a record the stats
    # don't use or without a position; None without the land (#79)
    inland: np.ndarray | None = field(default=None, repr=False, compare=False)

    @property
    def verdict(self) -> tuple[str, str]:
        """The checks in a line (#68): the worst status of those that could be made (pass or
        warn), and which didn't pass with their values, e.g. "2 of 7 checks warn: Completeness
        6.02 %; Longest gap 102103.500 s." Info checks are left out; n/a ones are named
        """
        judged = [c for c in self.checks if c.status in ("pass", "warn", "fail")]
        missing = [c.name for c in self.checks if c.status == "n/a"]
        total = len(judged) + len(missing)
        na = f" n/a: {', '.join(missing)}." if missing else ""
        if not judged:
            return "n/a", f"No check could be made.{na}"
        problems = [c for c in judged if c.status != "pass"]
        checks = "checks" if total > 1 else "check"
        if not problems:
            if not missing:
                return "pass", f"All {total} {checks} pass."
            verb = "pass" if len(judged) > 1 else "passes"
            return "pass", f"{len(judged)} of {total} {checks} {verb}.{na}"
        status = "fail" if any(c.status == "fail" for c in problems) else "warn"
        listed = "; ".join(f"{c.name} {c.value}" for c in problems)
        verb = status if len(problems) > 1 else f"{status}s"
        return status, f"{len(problems)} of {total} {checks} {verb}: {listed}.{na}"

    def __str__(self) -> str:
        statuses = [check.status for check in self.checks]
        return block(f"QA statistics: {counts(statuses)}", [str(c) for c in self.checks])


# What each test counts, by NavStats.counted's keys, as the per-file table heads them
COUNTED = {
    "out_of_sequence": "Out of sequence",
    "bad_quality": "Bad quality",
    "unreasonable_speeds": "Unreasonable speeds",
    "unreasonable_accelerations": "Unreasonable accelerations",
    "gaps": "Gaps",
    "outside_area": "Outside area",
    "over_land": "Over land",
    "flagged": "Flagged by QC",
}
SKIPPED = "outside the cruise window"
SKIPPED_SENTENCES = "Skipped sentences"  # counts_by_file's column of what the reader couldn't use


def _percent(part: int, whole: int) -> float | None:
    return 100.0 * part / whole if whole else None


def _range(values) -> Range:
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if not len(values):
        return Range(None, None)
    return Range(float(values.min()), float(values.max()))


def modal_interval(steps: np.ndarray) -> float | None:
    """The most common step [s], to the millisecond; a tie goes to the shorter step"""
    ms = np.round(np.asarray(steps) * 1000).astype(np.int64)
    ms = ms[ms > 0]
    if not len(ms):
        return None
    values, frequency = np.unique(ms, return_counts=True)  # values ascending
    return float(values[np.argmax(frequency)]) / 1000


def compute(
    nav: RawNav,
    qc_result: QCResult,
    thresholds: Thresholds,
    extent: Extent | None = None,
    land: "Land | None" = None,
) -> NavStats:
    th = thresholds
    f = nav.frame
    usable = (qc_result.flags & int(UNUSABLE)) == 0
    in_sequence = usable & ((qc_result.flags & int(Flag.TIME_SEQUENCE)) == 0)
    n = int(usable.sum())
    t_all = geodesy.epoch_seconds(f["time"])
    t = t_all[in_sequence]
    lon = f["lon"].to_numpy(dtype=float)[in_sequence]
    lat = f["lat"].to_numpy(dtype=float)[in_sequence]

    steps = np.diff(t)
    interval = modal_interval(steps)
    if interval and len(t):
        # Interval slots in integer nanoseconds: in float seconds since 1970, a step that
        # isn't exact in binary (0.2 s) errs by ~1e-7 and one fix in five lands in its
        # neighbour's slot (SR2612: 80 % for an unbroken 5 Hz feed, #108)
        ns = f["time"][in_sequence].dt.as_unit("ns").astype("int64").to_numpy()
        interval_ns = round(interval * 1e9)  # whole milliseconds, so exact
        possible = int((ns[-1] - ns[0]) // interval_ns) + 1
        occupied = len(np.unique((ns - ns[0]) // interval_ns))
    else:
        possible = occupied = len(t)

    out_of_sequence = int((usable & ~in_sequence).sum())
    dated = (qc_result.flags & int(Flag.DATE_RANGE)) == 0
    quality_all = f["quality"].to_numpy(dtype=float, na_value=np.nan)
    quality = quality_all[dated]
    reported = ~np.isnan(quality)
    bad = int(((quality < th.min_quality) | (quality > th.max_quality))[reported].sum())

    segments = geodesy.segments(t, lon, lat) if len(t) > 1 else None
    all_speeds = segments.speed if segments else np.array([])
    speeds = all_speeds[~np.isnan(all_speeds)]
    at_fixes = acceleration_at_fixes(t, lon, lat, th.accel_time_base)
    acceleration = at_fixes[~np.isnan(at_fixes)]
    change = acceleration_at_fixes(t, lon, lat, th.accel_time_base, signed=True)

    # Which records each test counted: a test between two fixes at the later one
    records = len(f)
    later = np.flatnonzero(in_sequence)[1:]

    def at(index: np.ndarray, hits: np.ndarray) -> np.ndarray:
        mask = np.zeros(records, dtype=bool)
        mask[index[hits]] = True
        return mask

    with np.errstate(invalid="ignore"):
        counted = {
            "out_of_sequence": usable & ~in_sequence,
            "bad_quality": dated
            & ~np.isnan(quality_all)
            & ((quality_all < th.min_quality) | (quality_all > th.max_quality)),
            "unreasonable_speeds": at(later, all_speeds > th.max_speed),
            "unreasonable_accelerations": at(np.flatnonzero(in_sequence), at_fixes > th.max_accel),
            "gaps": at(later, steps >= th.max_gap),
            "flagged": qc_result.flags != 0,
        }
    if extent is not None:
        counted["outside_area"] = usable & _outside(
            extent, f["lat"].to_numpy(dtype=float), f["lon"].to_numpy(dtype=float)
        )

    inland = None
    if land is not None:
        inland = np.where(usable, land.inland(f["lon"], f["lat"]), np.nan)
        with np.errstate(invalid="ignore"):
            counted["over_land"] = inland > th.max_inland

    times = f["time"][in_sequence]
    stats = NavStats(
        records=n,
        first=times.iloc[0].to_pydatetime() if len(times) else None,
        last=times.iloc[-1].to_pydatetime() if len(times) else None,
        modal_interval=interval,
        possible_epochs=possible,
        occupied_epochs=occupied,
        completeness=_percent(occupied, possible),
        longest_gap=float(steps.max()) if len(steps) else None,
        gaps=int((steps >= th.max_gap).sum()),
        out_of_sequence=out_of_sequence,
        out_of_sequence_percent=_percent(out_of_sequence, n),
        quality_reported=int(reported.sum()),
        bad_quality=bad,
        bad_quality_percent=_percent(bad, int(reported.sum())),
        speeds=len(speeds),
        unreasonable_speeds=int((speeds > th.max_speed).sum()),
        unreasonable_speeds_percent=_percent(int((speeds > th.max_speed).sum()), len(speeds)),
        accelerations=len(acceleration),
        unreasonable_accelerations=int((acceleration > th.max_accel).sum()),
        unreasonable_accelerations_percent=_percent(
            int((acceleration > th.max_accel).sum()), len(acceleration)
        ),
        nsv=_range(f["nsat"].to_numpy(dtype=float, na_value=np.nan)[usable]),
        hdop=_range(f["hdop"].to_numpy(dtype=float)[usable]),
        altitude=_range(f["alt"].to_numpy(dtype=float)[usable]),
        speed=_range(speeds),
        acceleration=_range(change),
        good=_good_fixes(f, t_all, qc_result, th),
        counted=counted,
        inland=inland,
    )
    stats.checks = _checks(stats, qc_result, th, extent)
    return stats


def gaps(nav: RawNav, qc_result: QCResult, thresholds: Thresholds) -> pd.DataFrame:
    """Every step of `max_gap` or longer between in-sequence usable records, the longest first
    (ties in time order). Columns: start (the last fix before), end (the first fix after),
    length [s], file_before and file_after
    """
    f = nav.frame
    usable = (qc_result.flags & int(UNUSABLE)) == 0
    index = np.flatnonzero(usable & ((qc_result.flags & int(Flag.TIME_SEQUENCE)) == 0))
    steps = np.diff(geodesy.epoch_seconds(f["time"].iloc[index]))
    long = np.flatnonzero(steps >= thresholds.max_gap)
    before, after = index[long], index[long + 1]
    files = f["source_file"].astype(str).to_numpy() if "source_file" in f else None
    table = pd.DataFrame(
        {
            "start": f["time"].iloc[before].to_numpy(),
            "end": f["time"].iloc[after].to_numpy(),
            "length": steps[long],
            "file_before": files[before] if files is not None else "",
            "file_after": files[after] if files is not None else "",
        }
    )
    return table.sort_values("length", ascending=False, kind="stable").reset_index(drop=True)


def _good_fixes(f: pd.DataFrame, t_all: np.ndarray, qc_result: QCResult, th) -> GoodFixes:
    good = qc_result.flags == 0  # in sequence: QC flags the others
    t = t_all[good]
    lon = f["lon"].to_numpy(dtype=float)[good]
    lat = f["lat"].to_numpy(dtype=float)[good]
    speeds = geodesy.segments(t, lon, lat).speed if len(t) > 1 else np.array([])
    times = f["time"][good]
    return GoodFixes(
        records=int(good.sum()),
        first=times.iloc[0].to_pydatetime() if len(times) else None,
        last=times.iloc[-1].to_pydatetime() if len(times) else None,
        nsv=_range(f["nsat"].to_numpy(dtype=float, na_value=np.nan)[good]),
        hdop=_range(f["hdop"].to_numpy(dtype=float)[good]),
        altitude=_range(f["alt"].to_numpy(dtype=float)[good]),
        speed=_range(speeds),
        acceleration=_range(
            acceleration_at_fixes(t, lon, lat, th.accel_time_base, signed=True) if len(t) else []
        ),
    )


def _outside(extent: Extent, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Per position, whether it's outside the expected area by more than EXTENT_TOLERANCE;
    missing positions aren't
    """
    tolerance = EXTENT_TOLERANCE
    with np.errstate(invalid="ignore"):
        north_south = (lat >= extent.southernmost - tolerance) & (
            lat <= extent.northernmost + tolerance
        )
        west, east = extent.westernmost - tolerance, extent.easternmost + tolerance
        if extent.crosses_antimeridian:
            east_west = (lon >= west) | (lon <= east)
        else:
            east_west = (lon >= west) & (lon <= east)
    placed = ~(np.isnan(lat) | np.isnan(lon))
    return placed & ~(north_south & east_west)


def counts_by_file(nav: RawNav, stats: NavStats) -> pd.DataFrame:
    """What each test counted, per nav file in the order read, then the files the reader
    couldn't read, those skipped as outside the cruise window, and the total. Columns: file,
    fixes, skipped (sentences the reader couldn't use, #67), then COUNTED's labels (n/a, as
    missing, for the outside-area test without an expected area), and status
    """
    files = nav.frame["source_file"].astype(str).to_numpy()
    skipped = {name: sum(reasons.values()) for name, reasons in nav.skipped_lines.items()}
    rows = []
    for name in nav.files:
        mine = files == name
        row = {"file": name, "fixes": int(mine.sum()), SKIPPED_SENTENCES: skipped.get(name, 0)}
        for key, label in COUNTED.items():
            mask = stats.counted.get(key)
            row[label] = None if mask is None else int((mask & mine).sum())
        rows.append(row | {"status": "read"})
    total = {"file": "Total", "fixes": len(files), SKIPPED_SENTENCES: sum(skipped.values())}
    for key, label in COUNTED.items():
        mask = stats.counted.get(key)
        total[label] = None if mask is None else int(mask.sum())
    rows += [
        {"file": name, "status": f"not read: {why}"} for name, why in nav.unread_files.items()
    ]
    rows += [{"file": name, "status": SKIPPED} for name in nav.skipped_files]
    rows.append(total | {"status": ""})
    counts = ["fixes", SKIPPED_SENTENCES, *COUNTED.values()]
    table = pd.DataFrame(rows, columns=["file", *counts, "status"])
    return table.astype({c: "Int64" for c in counts})


def skipped_by_file(nav: RawNav) -> pd.DataFrame:
    """The sentences the reader couldn't use, per file and reason (#67)"""
    rows = [
        {"file": name, "reason": reason, "sentences": count}
        for name, reasons in nav.skipped_lines.items()
        for reason, count in reasons.items()
    ]
    return pd.DataFrame(rows, columns=["file", "reason", "sentences"])


def _status(ok: bool | None) -> str:
    return "n/a" if ok is None else ("pass" if ok else "warn")


def _checks(stats: NavStats, qc_result, th, extent) -> list[CheckResult]:
    def pct(value):
        return "n/a" if value is None else f"{value:.2f} %"

    def at_most(value, limit):
        return None if value is None else value <= limit

    checks = [
        CheckResult(
            "Completeness",
            _status(
                None if stats.completeness is None else stats.completeness >= th.min_completeness
            ),
            pct(stats.completeness),
            f"{stats.occupied_epochs} of {stats.possible_epochs} {stats.modal_interval} s epochs"
            if stats.modal_interval
            else "",
        ),
        CheckResult(
            "Longest gap",
            _status(at_most(stats.longest_gap, th.max_longest_gap)),
            "n/a"
            if stats.longest_gap is None
            else q.LONGEST_GAP.format_duration(stats.longest_gap, seconds=True),
            f"{stats.gaps} gaps of {th.max_gap:g} s or longer",
        ),
        CheckResult(
            "Records out of sequence",
            _status(at_most(stats.out_of_sequence_percent, th.max_out_of_sequence)),
            pct(stats.out_of_sequence_percent),
            f"{stats.out_of_sequence} records",
        ),
        CheckResult(
            "Bad quality indicator",
            _status(at_most(stats.bad_quality_percent, th.max_bad_quality)),
            pct(stats.bad_quality_percent),
            f"{stats.bad_quality} of {stats.quality_reported} records"
            if stats.quality_reported
            else "no quality indicator in the data",
        ),
        CheckResult(
            "Unreasonable speeds",
            _status(at_most(stats.unreasonable_speeds_percent, th.max_unreasonable_speeds)),
            pct(stats.unreasonable_speeds_percent),
            f"{stats.unreasonable_speeds} of {stats.speeds} above {th.max_speed:g} m s-1",
        ),
        CheckResult(
            "Unreasonable accelerations",
            _status(
                at_most(
                    stats.unreasonable_accelerations_percent, th.max_unreasonable_accelerations
                )
            ),
            pct(stats.unreasonable_accelerations_percent),
            f"{stats.unreasonable_accelerations} of {stats.accelerations} above "
            f"{th.max_accel:g} m s-2",
        ),
    ]
    if extent is None:
        checks.append(CheckResult("Within the expected area", "n/a", "no extent configured"))
    else:
        outside = int(stats.counted["outside_area"].sum())
        checks.append(
            CheckResult(
                "Within the expected area",
                _status(outside == 0),
                f"{outside} outside",
                str(extent),
            )
        )
    checks.append(_over_water(stats, th))
    flagged = int((qc_result.flags != 0).sum())
    by_flag = ", ".join(
        f"{f.name.lower()} {qc_result.count(f)}" for f in Flag if qc_result.count(f)
    )
    checks.append(CheckResult("Fixes flagged by QC", "info", str(flagged), by_flag))
    return checks


def _over_water(stats: NavStats, th) -> CheckResult:
    """No usable fix more than `max_inland` from Natural Earth's water (#79, #95). Fixes on land
    nearer than that are reported, not warned of: farther than `land_tolerance` (canals and
    channels the map lacks), and within it (ports)
    """
    name = "Over water"
    if stats.inland is None:
        return CheckResult(name, "n/a", "no coastline data", "run fetch-map-data")
    metres = lambda value: f"{q.DISTANCE.format(value)} {q.DISTANCE.unit}"
    inland = stats.inland
    with np.errstate(invalid="ignore"):
        over = inland > th.max_inland
        waterways = (inland > th.land_tolerance) & ~over
        ports = (inland > 0) & (inland <= th.land_tolerance)
    detail = [f"more than {metres(th.max_inland)} from Natural Earth's 1:10m coasts and rivers"]
    if over.any():
        detail.append(f"up to {metres(np.nanmax(inland))} inland")
    if waterways.any():
        deepest = np.nanmax(np.where(waterways, inland, np.nan))
        detail.append(
            f"{int(waterways.sum())} on land {metres(th.land_tolerance)} to "
            f"{metres(th.max_inland)} from water, up to {metres(deepest)} (waterways the map "
            "lacks)"
        )
    if ports.any():
        detail.append(
            f"{int(ports.sum())} on land within {metres(th.land_tolerance)} of water (ports)"
        )
    count = int(over.sum())
    return CheckResult(name, _status(count == 0), f"{count} over land", "; ".join(detail))
