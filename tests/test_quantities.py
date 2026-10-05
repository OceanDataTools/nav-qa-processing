import math
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nav_qa_processing import quantities as q


def test_names_are_unique_and_registered():
    columns = q.BESTRES_COLUMNS + q.ONE_MINUTE_COLUMNS + q.CONTROL_COLUMNS
    assert all(q.QUANTITIES[c.name] is c for c in columns)


def test_units_are_standard():
    allowed = {None, "1", "s", "m", "m s-1", "m s-2", "degree", "degrees_east", "degrees_north",
               "percent"}  # fmt: skip
    assert {quantity.unit for quantity in q.QUANTITIES.values()} <= allowed


def test_geocsv_unit_and_label():
    assert q.ISO_TIME.geocsv_unit == "ISO_8601"
    assert q.SPEED_MADE_GOOD.geocsv_unit == "m s-1"
    assert q.SPEED_MADE_GOOD.label == "Speed over ground, from successive positions [m s-1]"
    assert q.ISO_TIME.label == "Date and time (UTC)"


def test_fixed_precision_is_never_trimmed():
    assert q.SPEED_MADE_GOOD.format(3.3) == "3.30"
    assert q.COURSE_MADE_GOOD.format(155.9) == "155.900"
    assert q.PERCENTAGE.format(95) == "95.00"
    assert q.DURATION.format(7) == "7.000"


def test_negative_zero_is_written_as_zero():
    assert q.HORIZONTAL_ACCELERATION.format(-0.0001) == "0.000"
    assert q.HORIZONTAL_ACCELERATION.format(-0.898) == "-0.898"


def test_receiver_precision_must_be_passed():
    with pytest.raises(ValueError, match="receiver's precision"):
        q.SHIP_LATITUDE.format(32.7)
    # R2R's control file writes 32.702744442; the standard keeps the receiver's 10 decimals
    assert q.SHIP_LATITUDE.format(32.702744442, decimals=10) == "32.7027444420"
    assert q.ANTENNA_HEIGHT.format(7.76, decimals=3) == "7.760"
    assert q.HDOP.format(1, decimals=1) == "1.0"


def test_receiver_decimals():
    assert q.receiver_decimals(["7.887", "7.76", "8", "", None]) == 3
    assert q.receiver_decimals(["3242.16878136"]) == 8
    assert q.receiver_decimals([]) == 0


def test_integers():
    assert q.NSV.format(13) == "13"
    assert q.NSV.format(np.int64(13)) == "13"
    assert q.COUNT.format(6.0) == "6"
    with pytest.raises(ValueError, match="integer quantity"):
        q.NSV.format(6.5)


@pytest.mark.parametrize(
    "missing", [None, math.nan, np.nan, pd.NA, pd.NaT, np.datetime64("NaT", "ns")]
)
def test_missing_values(missing):
    assert q.HDOP.format(missing, decimals=1) == q.MISSING
    assert q.NSV.format(missing) == q.MISSING
    assert q.ISO_TIME.format(missing, decimals=2) == q.MISSING


def test_datetimes():
    stamp = pd.Timestamp("2026-07-09T15:11:57.50", tz="UTC")
    assert q.ISO_TIME.format(stamp, decimals=2) == "2026-07-09T15:11:57.50Z"
    assert q.ISO_TIME.format(stamp, decimals=0) == "2026-07-09T15:11:58Z"  # half up
    assert q.ISO_TIME.format(np.datetime64("2026-07-09T15:11:57.510016"), 6) == (
        "2026-07-09T15:11:57.510016Z"
    )
    assert q.ISO_TIME.format(pd.Timestamp("2026-07-09T00:00:00.123456789Z"), 9) == (
        "2026-07-09T00:00:00.123456789Z"
    )
    # other time zones are converted to UTC
    pacific = datetime(2026, 7, 9, 8, 11, 57, 500000, tzinfo=timezone(timedelta(hours=-7)))
    assert q.ISO_TIME.format(pacific, decimals=1) == "2026-07-09T15:11:57.5Z"
    # rounding carries into the next day
    late = datetime(2026, 7, 9, 23, 59, 59, 999000, tzinfo=UTC)
    assert q.ISO_TIME.format(late, decimals=2) == "2026-07-10T00:00:00.00Z"


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        q.ISO_TIME.format(datetime(2026, 7, 9), decimals=0)  # noqa: DTZ001
