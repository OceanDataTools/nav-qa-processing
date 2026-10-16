"""Quality control: flag bad fixes, never drop them (NavManager's navqc, done soundly).

`flag(nav, thresholds, start, end)` gives every record a bitmask of `Flag`s. A record with any
bit set is flagged: written with a leading `#` in bestres and left out of the 1min and control
products and of SOG/COG. The rules, in the order they run:

1. Per record:
   - `DATE_RANGE`: before `gps_start` or after `gps_end`, or more than `date_margin_days` outside the
     cruise's [start, end];
   - `NULL_ISLAND`: position exactly 0, 0 (NavManager only when also at 00:00:00);
   - `POSITION`: longitude or latitude missing, or outside [-180, 180) and [-90, 90];
   - `BAD_FIX`: GGA quality outside [min_quality, max_quality], fewer than min_satellites or
     more than max_satellites, or HDOP of 0 or over max_hdop. Only checked when quality and
     satellite count are reported (RMC-only formats have neither); HDOP only when reported;
   - `ALTITUDE`: |altitude| ≥ max_altitude, when reported.
2. `TIME_SEQUENCE`: a time not after the last in-sequence time, or more than
   `max_time_step_days`
   after it. Every record that passes advances the clock, whatever its other flags.
3. `UNREACHABLE`, among the fixes not yet flagged: a fix the ship couldn't have reached from the
   track, i.e. farther from the last trusted fix than `max_reach_speed` times the time between
   them, plus `reach_tolerance`. The good fixes are split into segments wherever one fix can't
   be reached from the one before; the segment with the most fixes is trusted, and each segment
   after (before) it is trusted only if it can be reached from (can reach) the trusted track's
   end. A rejected segment is flagged whole, and the next is tested against the same trusted
   fix. This catches what the acceleration rule reports as two steps and leaves: a receiver
   holding a wrong position for minutes, a jump to near 0, 0, a bad first fix. A gap the ship
   really crossed is reachable. `max_reach_speed` (25 m s-1) is far above any ship's speed: it
   flags impossible positions, it isn't a speed limit. The tolerance leaves jumps of a few
   hundred metres to the acceleration rule (SP2220's degraded fixes jump up to 77 m in 0.5 s).
4. Among the fixes not yet flagged, in time order:
   - `STALE_REPEAT`: the same position as the previous good fix while the median of the
     `stale_window` preceding speeds exceeds `stale_speed`: a logger repeating a stale position
     while the ship moves;
   - `ACCELERATION`: the horizontal acceleration *at* each good fix, as a vector: the velocity
     from the last good fix at least `accel_time_base` earlier, the velocity to the first good
     fix at least that much later, and the magnitude of their difference over half the span.
     The time base keeps GNSS noise at high sample rates from tripping the test, but it blurs
     *which* fixes are wrong, and it can't by itself tell bad fixes from a track that *steps*
     (e.g. a DGPS correction moving every later fix 2 m). So each peak over the limit (the
     largest within one time base) is resolved geometrically, from the track more than one
     time base before and after it. The chord from the last fix before to the first fix after
     moves at the same velocity as the track on either side if the track comes back; if it
     steps, the chord's velocity is off by step / chord duration. A step larger than the
     tolerance (`max_accel` · time_base² / 4, 1 m by default) is reported in `notes` and
     nothing is flagged. Otherwise the fixes between that lie off the chord by more than the
     tolerance are flagged. A ship turning at 0.25 m s-2 bends the chord by only ~0.5 m.
     This repeats over the whole track until no unresolved peak is left (or `MAX_ITERATIONS`).
     The limit is raised, and the raise reported, when the coordinates' resolution makes
     quantization alone look like acceleration over the time base.

Different from NavManager, which this doesn't reproduce (`CLAUDE.md`, "What parity with R2R
means"):
- No 3,600-record buffers: no flag depends on where a buffer boundary falls.
- Speed doesn't flag fixes: NavManager's speed rule is commented out, and a fixed top speed suits
  no fleet. `Thresholds.max_speed` is for the QA test.
- NavManager's acceleration check computes speeds only from already-flagged fixes in its main
  loop (a flipped condition), so on clean data it almost never fires; its end-of-file loop
  checks every pair. It uses speed magnitudes, which miss a sideways jump, and flags the fix
  nearest the time of each excessive acceleration between two speeds. Here every good fix is
  checked, with velocity vectors, over a time base so that GNSS noise at high sample rates isn't
  flagged, and steps in the track are told apart from excursions. SP2424 (2 Hz, 150,925 fixes):
  R2R flags nothing; this flags 33 fixes in six 2-4 s excursions of 1-2 m and reports three
  steps, one of them a 3.7 m jump at the dock.
- NavManager iterates at most twice, and silently sets the acceleration limit to 3× the position
  resolution [m] when that is 1.11 m or more, judged from one record.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntFlag

import numpy as np
import pandas as pd

from nav_qa_processing import geodesy
from nav_qa_processing.config import Thresholds
from nav_qa_processing.rawnav import RawNav

MAX_ITERATIONS = 50
_METRES_PER_DEGREE = 111_320.0  # [m], of latitude, near enough for a resolution estimate


class Flag(IntFlag):
    DATE_RANGE = 1
    NULL_ISLAND = 2
    POSITION = 4
    BAD_FIX = 8
    ALTITUDE = 16
    TIME_SEQUENCE = 32
    STALE_REPEAT = 64
    ACCELERATION = 128
    UNREACHABLE = 256


DESCRIPTIONS = {
    Flag.DATE_RANGE: "Outside the valid GPS dates or the cruise dates",
    Flag.NULL_ISLAND: "Position 0, 0",
    Flag.POSITION: "Longitude or latitude missing or out of range",
    Flag.BAD_FIX: "Invalid GGA fix (quality, satellites or HDOP)",
    Flag.ALTITUDE: "Antenna altitude out of range",
    Flag.TIME_SEQUENCE: "Time not after the previous fix, or too far after it",
    Flag.STALE_REPEAT: "Repeated position while under way",
    Flag.ACCELERATION: "Unreasonable horizontal acceleration",
    Flag.UNREACHABLE: "Too far from the track to reach at max_reach_speed",
}


@dataclass
class QCResult:
    flags: np.ndarray  # uint16 bitmask per record
    accel_limit: float  # [m s-2] the limit used, after any allowance for resolution
    notes: list[str] = field(default_factory=list)
    # Steps in the track QC resolved without flagging: (time [s since 1970], size [m])
    steps: list[tuple[float, float]] = field(default_factory=list)

    @property
    def good(self) -> np.ndarray:
        return self.flags == 0

    def count(self, flag: Flag) -> int:
        return int(np.count_nonzero(self.flags & flag))


def flag(
    nav: RawNav,
    thresholds: Thresholds | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> QCResult:
    """QC flags for every record of `nav`; `start` and `end` are the cruise dates, if known"""
    th = thresholds or Thresholds()
    f = nav.frame
    n = len(f)
    flags = np.zeros(n, dtype=np.uint16)
    notes: list[str] = []
    t = geodesy.epoch_seconds(f["time"])
    lon = f["lon"].to_numpy(dtype=float)
    lat = f["lat"].to_numpy(dtype=float)

    flags[_outside_dates(f["time"], th, start, end)] |= np.uint16(Flag.DATE_RANGE)
    flags[(lon == 0) & (lat == 0)] |= np.uint16(Flag.NULL_ISLAND)
    bad_position = ~((lon >= -180) & (lon < 180) & (lat >= -90) & (lat <= 90))  # NaN is bad
    flags[bad_position] |= np.uint16(Flag.POSITION)
    flags[_bad_fix(f, th)] |= np.uint16(Flag.BAD_FIX)
    alt = f["alt"].to_numpy(dtype=float)
    flags[np.abs(alt) >= th.max_altitude] |= np.uint16(Flag.ALTITUDE)  # NaN compares False
    flags[_out_of_sequence(t, th)] |= np.uint16(Flag.TIME_SEQUENCE)
    unreachable, stretches = _unreachable(t, lon, lat, flags == 0, th)
    flags[unreachable] |= np.uint16(Flag.UNREACHABLE)
    if stretches:
        notes.append(
            f"{int(unreachable.sum())} fix(es) in {stretches} stretch(es) couldn't be reached "
            f"from the track at {th.max_reach_speed:g} m s-1"
        )

    good = flags == 0
    flags[_stale_repeats(t, lon, lat, good, th)] |= np.uint16(Flag.STALE_REPEAT)

    accel_limit = th.max_accel
    resolution = _resolution(nav, lat)
    noise = 4 * resolution / th.accel_time_base**2  # [m s-2] quantization alone
    if noise > accel_limit:
        accel_limit = noise
        notes.append(
            f"Coordinates resolve {resolution:.2f} m, so the acceleration limit is raised from "
            f"{th.max_accel} to {accel_limit:.2f} m s-2"
        )
    bad_accel, iterations, steps = _acceleration(
        t, lon, lat, flags == 0, th.accel_time_base, accel_limit
    )
    flags[bad_accel] |= np.uint16(Flag.ACCELERATION)
    if iterations >= MAX_ITERATIONS:
        notes.append(f"Acceleration check stopped after {MAX_ITERATIONS} iterations")
    if steps:
        notes.append(
            f"{len(steps)} position step(s) of up to {max(s for _, s in steps):.1f} m: the track "
            "shifts and doesn't come back, so no fix is flagged"
        )
    return QCResult(flags, accel_limit, notes, sorted(steps))


def _outside_dates(times: pd.Series, th: Thresholds, start, end) -> np.ndarray:
    t = pd.DatetimeIndex(times)
    gps_start, gps_end = (pd.Timestamp(d, tz="UTC") for d in (th.gps_start, th.gps_end))
    margin = pd.Timedelta(days=th.date_margin_days)
    outside = (t < gps_start) | (t >= gps_end + pd.Timedelta(days=1))
    if start is not None:
        outside |= t < _utc(start) - margin
    if end is not None:
        outside |= t > _utc(end) + margin
    return np.asarray(outside)


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp


def _bad_fix(f: pd.DataFrame, th: Thresholds) -> np.ndarray:
    quality = f["quality"].to_numpy(dtype=float, na_value=np.nan)
    nsat = f["nsat"].to_numpy(dtype=float, na_value=np.nan)
    hdop = f["hdop"].to_numpy(dtype=float)
    reported = ~np.isnan(quality) & ~np.isnan(nsat)
    bad = (quality < th.min_quality) | (quality > th.max_quality)
    bad |= (nsat < th.min_satellites) | (nsat > th.max_satellites)
    bad |= (hdop == 0) | (hdop > th.max_hdop)  # NaN HDOP compares False
    return reported & bad


def _out_of_sequence(t: np.ndarray, th: Thresholds) -> np.ndarray:
    """Times not after the last in-sequence time, or more than max_time_step_days after it"""
    bad = np.zeros(len(t), dtype=bool)
    if not len(t):
        return bad
    limit = th.max_time_step_days * 86_400.0  # [s]
    last = t[0]
    for i in range(1, len(t)):  # sequential by nature: what passes moves the reference
        if last < t[i] <= last + limit:
            last = t[i]
        else:
            bad[i] = True
    return bad


def _unreachable(t, lon, lat, good, th: Thresholds) -> tuple[np.ndarray, int]:
    """Good fixes the ship couldn't have reached from the trusted track, and how many
    stretches they're in
    """
    bad = np.zeros(len(t), dtype=bool)
    index = np.flatnonzero(good)
    if len(index) < 2:
        return bad, 0

    def reachable(i: int, j: int) -> bool:
        """Whether fix j (later) can be reached from fix i"""
        distance, _ = geodesy.inverse(lon[i], lat[i], lon[j], lat[j])
        return bool(distance <= th.max_reach_speed * (t[j] - t[i]) + th.reach_tolerance)

    a, b = index[:-1], index[1:]
    distance, _ = geodesy.inverse(lon[a], lat[a], lon[b], lat[b])
    breaks = np.flatnonzero(distance > th.max_reach_speed * (t[b] - t[a]) + th.reach_tolerance)
    if not len(breaks):
        return bad, 0
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks + 1, [len(index)]])  # positions in index, exclusive
    seed = int(np.argmax(ends - starts))
    rejected = 0
    last = index[ends[seed] - 1]
    for s, e in zip(starts[seed + 1 :], ends[seed + 1 :], strict=True):
        if reachable(last, index[s]):
            last = index[e - 1]
        else:
            bad[index[s:e]] = True
            rejected += 1
    first = index[starts[seed]]
    for s, e in zip(starts[:seed][::-1], ends[:seed][::-1], strict=True):
        if reachable(index[e - 1], first):
            first = index[s]
        else:
            bad[index[s:e]] = True
            rejected += 1
    return bad, _stretches(bad) if rejected else 0


def _stretches(bad: np.ndarray) -> int:
    """Runs of flagged records"""
    return int(np.count_nonzero(np.diff(np.concatenate([[0], bad.astype(int)])) == 1))


def _stale_repeats(t, lon, lat, good, th: Thresholds) -> np.ndarray:
    """Good fixes repeating the previous good fix's position while the median of the preceding
    speeds says the ship is moving
    """
    bad = np.zeros(len(t), dtype=bool)
    index = np.flatnonzero(good)
    if len(index) < th.stale_window + 2:
        return bad
    speeds: list[float] = []
    previous = index[0]
    for i in index[1:]:
        repeated = lon[i] == lon[previous] and lat[i] == lat[previous]
        moving = len(speeds) >= th.stale_window and (
            np.median(speeds[-th.stale_window :]) > th.stale_speed
        )
        if repeated and moving:
            bad[i] = True
            continue  # keep comparing with the last position that wasn't stale
        distance, _ = geodesy.inverse(lon[previous], lat[previous], lon[i], lat[i])
        elapsed = t[i] - t[previous]
        if elapsed > 0:
            speeds.append(float(distance) / elapsed)
        previous = i
    return bad


def _resolution(nav: RawNav, lat: np.ndarray) -> float:
    """The coordinates' resolution [m]: one unit in the last recorded decimal place"""
    lat_m = 10.0 ** -nav.decimals.get("lat", 0) * _METRES_PER_DEGREE
    mean_lat = np.nanmean(lat) if np.isfinite(lat).any() else 0.0
    lon_m = 10.0 ** -nav.decimals.get("lon", 0) * _METRES_PER_DEGREE * np.cos(np.radians(mean_lat))
    return float(max(lat_m, lon_m))


def _acceleration(
    t, lon, lat, good, time_base, limit
) -> tuple[np.ndarray, int, list[tuple[float, float]]]:
    """Fixes flagged for acceleration, the iterations it took, and each step in the track that
    exceeds the limit: (time [s since 1970], size [m])
    """
    flagged = np.zeros(len(t), dtype=bool)
    good = good.copy()
    settled: list[tuple[float, float]] = []  # stretches resolved without flagging
    steps: list[tuple[float, float]] = []
    for iteration in range(1, MAX_ITERATIONS + 1):
        index = np.flatnonzero(good)
        tg = t[index]
        acceleration = acceleration_at_fixes(tg, lon[index], lat[index], time_base)
        over = np.flatnonzero(acceleration > limit)  # NaN at the ends compares False
        for start, end in settled:
            over = over[(tg[over] < start) | (tg[over] > end)]
        if not len(over):
            return flagged, iteration, steps
        peaks = over[_local_peaks(tg[over], acceleration[over], time_base)]
        for peak in peaks[np.argsort(-acceleration[peaks])]:
            if any(start <= tg[peak] <= end for start, end in settled):
                continue
            bad, step = _resolve(t, lon, lat, good, tg[peak], time_base, limit)
            if len(bad):
                flagged[bad] = True
                good[bad] = False
            else:
                settled.append(_stretch(tg, acceleration > limit, peak, time_base))
                if step is not None:
                    steps.append((float(tg[peak]), step))
    return flagged, MAX_ITERATIONS, steps


def _stretch(t, over, peak, gap) -> tuple[float, float]:
    """The times spanned by the run of over-limit fixes around `peak`, allowing gaps up to
    `gap` seconds between them
    """
    lo = hi = peak
    while lo > 0 and over[lo - 1] and t[lo] - t[lo - 1] <= gap:
        lo -= 1
    while hi < len(t) - 1 and over[hi + 1] and t[hi + 1] - t[hi] <= gap:
        hi += 1
    return float(t[lo]), float(t[hi])


def _resolve(t, lon, lat, good, peak_time, time_base, limit) -> tuple[np.ndarray, float | None]:
    """For a peak of acceleration: the fixes (record numbers) to flag, and the size [m] of the
    step in the track if it steps (then nothing is flagged)
    """
    tolerance = limit * time_base**2 / 4  # [m]
    gb = good & (t >= peak_time - 3 * time_base) & (t <= peak_time - time_base)
    ga = good & (t >= peak_time + time_base) & (t <= peak_time + 3 * time_base)
    before, after = np.flatnonzero(gb), np.flatnonzero(ga)
    if len(before) < 2 or len(after) < 2:
        return np.array([], dtype=int), None  # not enough track around it to tell
    x, y = _local_metres(lon, lat, lat[before[-1]])
    p, q = before[-1], after[0]

    def velocity(i, j):
        return np.array([x[j] - x[i], y[j] - y[i]]) / (t[j] - t[i])

    sides = 0.5 * (velocity(before[0], p) + velocity(q, after[-1]))
    step = float(np.hypot(*(velocity(p, q) - sides)) * (t[q] - t[p]))
    if step > tolerance:
        return np.array([], dtype=int), step
    between = np.flatnonzero(good & (t > t[p]) & (t < t[q]))
    share = (t[between] - t[p]) / (t[q] - t[p])
    off = np.hypot(
        x[between] - (x[p] + share * (x[q] - x[p])), y[between] - (y[p] + share * (y[q] - y[p]))
    )
    return between[off > tolerance], None


def _local_metres(lon, lat, lat0) -> tuple[np.ndarray, np.ndarray]:
    """East and north coordinates [m], flat-earth around latitude `lat0`; fine over seconds"""
    x = lon * _METRES_PER_DEGREE * np.cos(np.radians(lat0))
    y = lat * _METRES_PER_DEGREE
    return x, y


def acceleration_at_fixes(t, lon, lat, time_base, signed: bool = False) -> np.ndarray:
    """Horizontal acceleration [m s-2] at each fix, from the velocities over at least
    `time_base` before and after it, divided by half the span: the magnitude of the change in
    velocity, or with `signed` the change in speed (negative when slowing). NaN where there
    isn't that much track on both sides
    """
    n = len(t)
    acceleration = np.full(n, np.nan)
    if n < 3:
        return acceleration
    before = np.searchsorted(t, t - time_base + 1e-9, side="right") - 1  # last fix ≥ base earlier
    after = np.searchsorted(t, t + time_base - 1e-9)  # first fix ≥ base later
    k = np.flatnonzero((before >= 0) & (after < n))
    if not len(k):
        return acceleration
    i, j = before[k], after[k]
    v_in = _velocity(t[i], lon[i], lat[i], t[k], lon[k], lat[k])
    v_out = _velocity(t[k], lon[k], lat[k], t[j], lon[j], lat[j])
    change = np.hypot(*v_out) - np.hypot(*v_in) if signed else np.hypot(*(v_out - v_in))
    acceleration[k] = change / (0.5 * (t[j] - t[i]))
    return acceleration


def _velocity(t1, lon1, lat1, t2, lon2, lat2) -> np.ndarray:
    """East and north velocity components [m s-1], shape (2, n)"""
    distance, azimuth = geodesy.inverse(lon1, lat1, lon2, lat2)
    speed = distance / (t2 - t1)
    rad = np.radians(azimuth)
    return np.array([speed * np.sin(rad), speed * np.cos(rad)])


def _local_peaks(times: np.ndarray, sizes: np.ndarray, reach: float) -> np.ndarray:
    """Indices into `times` (ascending) of the entries largest within ±`reach` of themselves"""
    lo = np.searchsorted(times, times - reach, side="left")
    hi = np.searchsorted(times, times + reach, side="right")
    return np.array(
        [k for k in range(len(times)) if sizes[k] >= sizes[lo[k] : hi[k]].max()], dtype=int
    )
