"""QA configuration. So far only the thresholds; the cruise TOML (and overriding thresholds from
its `[thresholds]` table) comes with Phase 3.

Defaults are NavManager's (`include/globals.inc.php`, `navtools.inc.php`, `navqc.inc.php`) except
where noted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Thresholds:
    # -- QA tests (Phase 3); speed isn't a QC rule: a vessel's top speed varies and currents
    #    change speed over ground, so acceleration is the better indicator of a bad fix --
    max_speed: float = 8.7  # [m s-1]
    max_gap: float = 300.0  # [s]

    # -- QC --
    max_accel: float = 1.0  # [m s-2]
    # Acceleration at a fix uses velocities over at least this long before and after it (not in
    # NavManager): a fix displaced by d metres shows as ~2 d / time_base² m s-2, so 2 s ignores
    # GNSS noise at high sample rates but still catches jumps of 2 m or more
    accel_time_base: float = 2.0  # [s]
    gps_start: datetime = datetime(1994, 3, 1, tzinfo=UTC)
    gps_end: datetime = datetime(2034, 12, 31, tzinfo=UTC)
    date_margin: timedelta = timedelta(days=30)  # allowed outside the cruise dates
    max_time_step: timedelta = timedelta(days=30)  # largest step forward in time
    # NMEA GGA fix quality: 0 invalid, 6 dead reckoning, 7 manual input, 8 simulator
    min_quality: int = 1
    max_quality: int = 5
    min_satellites: int = 4
    max_satellites: int = 50
    max_hdop: float = 50.0
    max_altitude: float = 500.0  # [m], magnitude
    stale_speed: float = 0.25  # [m s-1] median speed above which a repeated position is stale
    stale_window: int = 10  # [speeds] for that median
