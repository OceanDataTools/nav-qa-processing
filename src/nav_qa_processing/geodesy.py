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

Deliberately not reproduced (the project matches R2R's product formats, not NavManager's
arithmetic):
- NavManager computes from coordinates as written in r2rnav, rounded to the receiver's precision;
  this module uses them at full precision.
- navsogcog reads 86,400 records at a time, and in every full buffer it divides the *previous*
  segment's distance by the current elapsed time. R2R's products carry this: SP2424's first
  86,399 speeds lag the ship by one fix.
"""

from dataclasses import dataclass

import numpy as np
from pyproj import Geod

_WGS84 = Geod(ellps="WGS84")
# Points per pyproj call: a whole cruise at once (3.5 M fixes) takes a dozen temporary arrays
# of its length; chunks of this many give the same values in bounded memory (#157)
CHUNK = 500_000


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
    if lon1.ndim != 1 or len(lon1) <= CHUNK:
        return _inverse(lon1, lat1, lon2, lat2)
    distance, azimuth = np.empty(len(lon1)), np.empty(len(lon1))
    for start in range(0, len(lon1), CHUNK):
        part = slice(start, start + CHUNK)
        distance[part], azimuth[part] = _inverse(lon1[part], lat1[part], lon2[part], lat2[part])
    return distance, azimuth


def _inverse(lon1, lat1, lon2, lat2) -> tuple[np.ndarray, np.ndarray]:
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


def sog_cog(times, lon, lat, good=None) -> tuple[np.ndarray, np.ndarray]:
    """Per-record speed [m s-1] and course [degree] over ground, as NavManager's navsogcog
    defines them: each good fix gets the segment to the next good fix; the last good fix repeats
    the previous one's values; flagged fixes (`good` False) and a lone good fix get NaN. `good`
    defaults to every fix.
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
    sog[index[:-1]] = seg.speed
    cog[index[:-1]] = seg.course
    sog[index[-1]] = seg.speed[-1]
    cog[index[-1]] = seg.course[-1]
    return sog, cog
