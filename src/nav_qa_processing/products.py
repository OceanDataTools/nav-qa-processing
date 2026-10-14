"""The three R2R navigation products: bestres, 1min and control (NavManager's navsogcog,
navsample and NavControl).

- `bestres(nav, qc_result)`: every pre-QC record with its QC flags and per-record SOG and COG
  (`geodesy.sog_cog`, computed over good fixes only; flagged records get none).
- `sample(bestres, interval)`: for each whole multiple of `interval` (60 s for 1min) within half
  an interval of the good fixes, the good record nearest to it, if within half an interval;
  records are never interpolated, and keep their bestres SOG and COG. A tie goes to the earlier
  record. (A boundary just before the first fix counts: SP2424's 1min starts with its first fix,
  06:19:01, for 06:19:00.)
- `control(one_minute, tolerance)`: the 1min records Douglas–Peucker keeps with a planar
  tolerance of 0.01° on longitude and latitude (shapely's `simplify`). Longitudes are unwrapped
  first, so a track across the antimeridian isn't a 360° jump.

Checked against R2R's SP2613 products: 1min is the same 475 records, control the same 5 points.

Different from NavManager, which this doesn't reproduce (`CLAUDE.md`, "What parity with R2R
means"):
- navsample's nodes are always whole minutes whatever the interval, its window is ≤ 29 s after
  a node but ≤ 30 s before it, and its 3,600-record ring buffer forgets the record before each
  wrap, so a node bracketed across the wrap gets no record.
- R2R's control comes from NavManager's `navsimplifier.py`, a recursive Douglas–Peucker with the
  same planar distance; its committed tolerance is 0.1°, but R2R's products use 0.01°.
- R2R's processing description calls 1min "the first valid record of each minute"; navsample
  takes the record nearest each minute, as here.
"""

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString

from nav_qa_processing import geodesy
from nav_qa_processing.qc import QCResult
from nav_qa_processing.rawnav import RawNav

ONE_MINUTE = 60.0  # [s]
CONTROL_TOLERANCE = 0.01  # [degree], planar on lon/lat

RECORD_COLUMNS = ["time", "lon", "lat", "quality", "nsat", "hdop", "alt"]


def bestres(nav: RawNav, qc_result: QCResult) -> pd.DataFrame:
    """Every record, with `flags` (0 for a good fix), `sog` [m s-1] and `cog` [degree]"""
    frame = nav.frame[RECORD_COLUMNS].copy()
    frame["flags"] = qc_result.flags
    sog, cog = geodesy.sog_cog(
        frame["time"], frame["lon"].to_numpy(), frame["lat"].to_numpy(), good=qc_result.good
    )
    frame["sog"] = sog
    frame["cog"] = cog
    return frame


def sample(best: pd.DataFrame, interval: float = ONE_MINUTE) -> pd.DataFrame:
    """The good record nearest each whole multiple of `interval`, within half an interval"""
    good = best[best["flags"] == 0]
    columns = ["time", "lon", "lat", "sog", "cog"]
    if good.empty:
        return good[columns].reset_index(drop=True)  # empty, with bestres's column types
    t = geodesy.epoch_seconds(good["time"])
    first, last = np.round((t[0] / interval, t[-1] / interval))  # nodes within half an interval
    nodes = np.arange(first, last + 1) * interval
    after = np.searchsorted(t, nodes)  # first record at or after each node
    before = np.clip(after - 1, 0, len(t) - 1)
    after = np.clip(after, 0, len(t) - 1)
    # the nearer of the records either side; a tie goes to the earlier one
    use_after = np.abs(t[after] - nodes) < np.abs(nodes - t[before])
    nearest = np.where(use_after, after, before)
    close = np.abs(t[nearest] - nodes) <= interval / 2
    chosen = np.unique(nearest[close])
    return good.iloc[chosen][columns].reset_index(drop=True)


def control(one_minute: pd.DataFrame, tolerance: float = CONTROL_TOLERANCE) -> pd.DataFrame:
    """The 1min records kept by Douglas–Peucker with a planar `tolerance` [degree]"""
    columns = ["time", "lon", "lat"]
    if len(one_minute) < 3:
        return one_minute[columns].reset_index(drop=True)
    lon = np.degrees(np.unwrap(np.radians(one_minute["lon"].to_numpy(dtype=float))))
    lat = one_minute["lat"].to_numpy(dtype=float)
    index = np.arange(len(one_minute), dtype=float)  # carried through as z
    line = LineString(np.column_stack([lon, lat, index]))
    simple = shapely.simplify(line, tolerance, preserve_topology=False)
    kept = np.asarray(simple.coords)[:, 2].astype(int)
    return one_minute.iloc[kept][columns].reset_index(drop=True)
