"""Distance, speed, course and acceleration between fixes on the WGS-84 ellipsoid.

Geodesics come from pyproj (`Geod(ellps="WGS84").inv`, Karney's algorithm), vectorized over numpy
arrays. NavManager uses its own Vincenty implementation; the two differ by at most ~5 µm per step,
1e-5 m s-1 in speed and ~0.001° in course while under way, so only values right at a rounding
boundary come out differently (see `tests/test_geodesy.py` for the parity tolerance).

Definitions, matching NavManager (`navtools`, `navqc`, `navqa`, `navsogcog`):
- A segment joins two successive fixes. Its speed is distance / elapsed time [m s-1] and its
  course is the forward azimuth at the first fix [degree, clockwise from true north, 0–360).
  Coincident fixes have distance 0 and course 0, the value R2R publishes (pyproj gives 180).
- A segment with no elapsed time (or time running backwards) has no speed: NaN. NavManager
  divides by zero there; QC flags such records before it matters.
- A segment's speed belongs to the midpoint of its two times. Acceleration is the change in
  speed between successive segments divided by the time between their midpoints [m s-2].
- Per-record SOG and COG (`sog_cog`, NavManager's navsogcog) use only good fixes: each good fix
  takes the segment to the next good fix, and the last good fix repeats the one before it.
  Flagged fixes get NaN.
- NavManager computes from the coordinates as written in r2rnav, i.e. rounded to the receiver's
  precision. `as_written` reproduces that rounding; callers that need R2R's values apply it to
  lon and lat first.

navsogcog's buffer bug, which R2R's published products carry, is not reproduced by default; see
`sog_cog`.
"""

from dataclasses import dataclass

import numpy as np
from pyproj import Geod

_WGS84 = Geod(ellps="WGS84")
NAVSOGCOG_BUFFER = 86_400  # [records] navsogcog's buffer size


def epoch_seconds(times) -> np.ndarray:
    """Times as float seconds since 1970-01-01T00:00:00Z. Takes datetime64 values (naive ones
    are UTC), a pandas datetime Series or DatetimeIndex, or seconds already.
    """
    values = np.asarray(getattr(times, "values", times))
    if np.issubdtype(values.dtype, np.datetime64):
        ns = values.astype("datetime64[ns]").astype(np.int64)
        return ns / 1e9
    return values.astype(float)


def inverse(lon1, lat1, lon2, lat2) -> tuple[np.ndarray, np.ndarray]:
    """Distance [m] and forward azimuth [degree, 0–360) from each point 1 to point 2.

    Coincident points give distance 0 and azimuth 0. NaN in, NaN out.
    """
    lon1, lat1, lon2, lat2 = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (lon1, lat1, lon2, lat2))
    )
    azimuth, _, distance = _WGS84.inv(lon1, lat1, lon2, lat2)
    distance = np.asarray(distance, dtype=float)
    azimuth = np.where(distance == 0, 0.0, np.mod(np.asarray(azimuth, dtype=float), 360.0))
    missing = np.isnan(lon1) | np.isnan(lat1) | np.isnan(lon2) | np.isnan(lat2)
    return np.where(missing, np.nan, distance), np.where(missing, np.nan, azimuth)


@dataclass(frozen=True)
class Segments:
    """Speed and course of each segment between successive fixes: n fixes, n - 1 segments"""

    distance: np.ndarray  # [m]
    elapsed: np.ndarray  # [s]
    speed: np.ndarray  # [m s-1]
    course: np.ndarray  # [degree]
    time: np.ndarray  # midpoint [s since 1970]


def segments(times, lon, lat) -> Segments:
    """Segments joining each fix to the next"""
    t = epoch_seconds(times)
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    distance, course = inverse(lon[:-1], lat[:-1], lon[1:], lat[1:])
    elapsed = np.diff(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(elapsed > 0, distance / elapsed, np.nan)
    return Segments(distance, elapsed, speed, course, 0.5 * (t[:-1] + t[1:]))


def accelerations(segment_times, speeds) -> tuple[np.ndarray, np.ndarray]:
    """Acceleration between successive segments [m s-2] and the time it belongs to (the
    midpoint of the two segment times): n segments, n - 1 accelerations
    """
    t = np.asarray(segment_times, dtype=float)
    v = np.asarray(speeds, dtype=float)
    elapsed = np.diff(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        acceleration = np.where(elapsed > 0, np.diff(v) / elapsed, np.nan)
    return acceleration, 0.5 * (t[:-1] + t[1:])


def sog_cog(
    times, lon, lat, good=None, navmanager_buffer: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Per-record speed [m s-1] and course [degree] over ground, as NavManager's navsogcog.

    Each good fix gets the segment to the next good fix; the last good fix repeats the previous
    one's values; flagged fixes (`good` False) and a lone good fix get NaN. `good` defaults to
    every fix.

    Speeds are correct by default. NavManager's navsogcog isn't: it reads 86,400 records at a
    time, and each time a buffer fills it computes every speed in it from the *previous*
    segment's distance and the current elapsed time (0 for the very first segment, and for no
    elapsed time), then carries the buffer's last good fix into the next one; only what remains
    at the end of the file is right. R2R's products carry this (SP2424: all 86,399 speeds of the
    first buffer). `navmanager_buffer=NAVSOGCOG_BUFFER` reproduces it, for comparing with R2R.
    Courses aren't affected.
    """
    t = epoch_seconds(times)
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    n = len(t)
    good = np.ones(n, dtype=bool) if good is None else np.asarray(good, dtype=bool)
    sog = np.full(n, np.nan)
    cog = np.full(n, np.nan)
    index = np.flatnonzero(good)
    if len(index) < 2:
        return sog, cog
    seg = segments(t[index], lon[index], lat[index])
    speed = seg.speed
    if navmanager_buffer:
        stale = _full_buffer_segments(index, n, navmanager_buffer)
        previous_distance = np.concatenate(([0.0], seg.distance[:-1]))
        with np.errstate(divide="ignore", invalid="ignore"):
            buffered = np.where(seg.elapsed != 0, previous_distance / seg.elapsed, 0.0)
        speed = np.where(stale, buffered, speed)
    sog[index[:-1]] = speed
    cog[index[:-1]] = seg.course
    sog[index[-1]] = speed[-1]
    cog[index[-1]] = seg.course[-1]
    return sog, cog


def _full_buffer_segments(good_index: np.ndarray, n: int, size: int) -> np.ndarray:
    """Which segments (between successive good records) navsogcog computes in a full-buffer
    pass. `good_index` holds the record numbers of the good fixes; there are `n` records.
    """
    stale = np.zeros(len(good_index) - 1, dtype=bool)
    start = 0
    while start + size <= n:
        lo, hi = np.searchsorted(good_index, [start, start + size])
        if hi - lo >= 2:
            stale[lo : hi - 1] = True  # segments joining good fixes inside the buffer
        last_good = good_index[hi - 1] if hi > lo else start
        # the last good fix starts the next buffer; NavManager would loop forever without one
        start = last_good if last_good > start else start + size
    return stale


def as_written(values, decimals: int) -> np.ndarray:
    """`values` rounded the way text with `decimals` places rounds them, then read back as
    floats: what NavManager computes from after writing r2rnav
    """
    values = np.asarray(values, dtype=float)
    return np.array([float(f"{v:.{decimals}f}") for v in values]) if len(values) else values
