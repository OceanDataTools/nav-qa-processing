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
  (`min_quality` to `max_quality`, 1 to 5); none when no record reports one.
- **Speeds:** between successive in-sequence records; unreasonable above `max_speed`.
- **Accelerations:** QC's acceleration at each fix (velocity vectors over `accel_time_base`);
  unreasonable above `max_accel`, so QA and QC agree. The range is the signed rate of change of
  speed over the same time base.
- **Ranges:** NSV, HDOP and altitude over the records reporting them.

The core checks (pass/warn/info/n/a) compare these with `Thresholds`, whose defaults equal R2R's
green bounds; R2R's own G/Y/R ratings belong to the R2R integration.

Different from navqa (see docs/navmanager-differences.md, A2-A6): a count of interval slots
replaces its accumulated "countable epochs" (whose end-of-file loop compares milliseconds with
seconds); quality 3 (PPS) isn't bad; accelerations come from QC's definition rather than from
scalar speeds between every pair of records.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from nav_qa_processing import geodesy
from nav_qa_processing.config import Extent, Thresholds
from nav_qa_processing.qc import Flag, QCResult, acceleration_at_fixes
from nav_qa_processing.rawnav import RawNav
from nav_qa_processing.readable import Readable, block, counts, status_line

UNUSABLE = Flag.DATE_RANGE | Flag.POSITION | Flag.NULL_ISLAND
# [degree] about 1 cm: R2R's catalog rounds its cruise extent (the track's own) to 10 decimals,
# which can leave the fixes on its edges just outside it (SP2613: 3 by 3e-11°)
EXTENT_TOLERANCE = 1e-7


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

    def __str__(self) -> str:
        statuses = [check.status for check in self.checks]
        return block(f"QA statistics: {counts(statuses)}", [str(c) for c in self.checks])


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
    nav: RawNav, qc_result: QCResult, thresholds: Thresholds, extent: Extent | None = None
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
        possible = int(np.floor(1 + (t[-1] - t[0]) / interval + 1e-9))
        occupied = len(np.unique(np.floor((t - t[0]) / interval + 1e-9)))
    else:
        possible = occupied = len(t)

    out_of_sequence = int((usable & ~in_sequence).sum())
    quality = f["quality"].to_numpy(dtype=float, na_value=np.nan)[usable]
    reported = ~np.isnan(quality)
    bad = int(((quality < th.min_quality) | (quality > th.max_quality))[reported].sum())

    segments = geodesy.segments(t, lon, lat) if len(t) > 1 else None
    speeds = segments.speed[~np.isnan(segments.speed)] if segments else np.array([])
    acceleration = acceleration_at_fixes(t, lon, lat, th.accel_time_base)
    acceleration = acceleration[~np.isnan(acceleration)]
    change = acceleration_at_fixes(t, lon, lat, th.accel_time_base, signed=True)

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
    )
    stats.checks = _checks(stats, f[usable], qc_result, th, extent)
    return stats


def _status(ok: bool | None) -> str:
    return "n/a" if ok is None else ("pass" if ok else "warn")


def _checks(stats: NavStats, usable: pd.DataFrame, qc_result, th, extent) -> list[CheckResult]:
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
            "n/a" if stats.longest_gap is None else f"{stats.longest_gap:.3f} s",
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
        inside = [
            extent.contains(la, lo, EXTENT_TOLERANCE)
            for la, lo in zip(usable["lat"], usable["lon"], strict=True)
            if not (np.isnan(la) or np.isnan(lo))
        ]
        outside = len(inside) - sum(inside)
        checks.append(
            CheckResult(
                "Within the expected area",
                _status(outside == 0),
                f"{outside} outside",
                str(extent),
            )
        )
    flagged = int((qc_result.flags != 0).sum())
    by_flag = ", ".join(
        f"{f.name.lower()} {qc_result.count(f)}" for f in Flag if qc_result.count(f)
    )
    checks.append(CheckResult("Fixes flagged by QC", "info", str(flagged), by_flag))
    return checks
