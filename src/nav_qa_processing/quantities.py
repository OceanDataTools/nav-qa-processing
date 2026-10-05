"""The project-wide quantity table.

Every value this project writes (GeoCSV columns, QA XML results, bounds, infos and ranges, PDF
tables, figure axes, the notebook) is defined here once: name, description, datatype, unit and
format. Writers format values and label units only through this table. They never contain their
own format strings or unit names.

The rules (docs/architecture.md, "Standardized quantities"):
- Units are CF/UDUNITS strings. `1` is the unit of every dimensionless value. Datetimes have no
  unit; GeoCSV's `field_unit` for them is `ISO_8601`.
- Floats use fixed decimals and are never trimmed. A quantity either has a fixed precision, or
  takes the receiver's precision: the most decimals the receiver reported for that column across
  the whole fileset (see `receiver_decimals`). The caller passes the decimals in that case.
- Integers are written without decimals.
- Datetimes are UTC, `YYYY-MM-DDThh:mm:ss[.f…]Z`, at the receiver's time precision.
- Missing values are written `NAN`.

Names are R2R's identifiers (GeoCSV `field_standard_name`s) where R2R has one. Don't rename them.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import numpy as np

MISSING = "NAN"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
GEOCSV_DATETIME_UNIT = "ISO_8601"


class Datatype(StrEnum):
    """GeoCSV's datatype vocabulary"""

    DATETIME = "datetime"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"


@dataclass(frozen=True)
class Quantity:
    """One quantity: what it is, its unit and how its values are written.

    `decimals` is the fixed precision of a float or the fractional-second digits of a datetime.
    None means the receiver's precision, which the caller passes to `format`.
    """

    name: str
    description: str
    datatype: Datatype
    unit: str | None = None
    decimals: int | None = None

    @property
    def geocsv_unit(self) -> str:
        """The GeoCSV `field_unit` entry"""
        if self.datatype is Datatype.DATETIME:
            return GEOCSV_DATETIME_UNIT
        return self.unit or ""

    @property
    def label(self) -> str:
        """Description and unit for table headers and figure axes, e.g. "Speed over ground [m s-1]" """
        if self.unit is None:
            return self.description
        return f"{self.description} [{self.unit}]"

    def format(self, value, decimals: int | None = None) -> str:
        """`value` as text in this quantity's standard format, or MISSING.

        `decimals` overrides the fixed precision. It is required when the quantity takes the
        receiver's precision.
        """
        if _is_missing(value):
            return MISSING
        match self.datatype:
            case Datatype.STRING:
                return str(value)
            case Datatype.INTEGER:
                return _format_integer(self.name, value)
            case Datatype.FLOAT:
                return _format_float(float(value), self._decimals(decimals))
            case Datatype.DATETIME:
                return format_datetime(value, self._decimals(decimals))

    def _decimals(self, decimals: int | None) -> int:
        decimals = self.decimals if decimals is None else decimals
        if decimals is None:
            raise ValueError(f"{self.name} takes the receiver's precision: pass decimals")
        if decimals < 0:
            raise ValueError(f"{self.name}: decimals must be >= 0, not {decimals}")
        return decimals


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float | np.floating):
        return math.isnan(value)
    if isinstance(value, np.datetime64):
        return np.isnat(value)
    # pandas' NA and NaT are neither equal to nor of the type of anything else
    return type(value).__name__ in ("NAType", "NaTType")


def _format_integer(name: str, value) -> str:
    if isinstance(value, float | np.floating) and not float(value).is_integer():
        raise ValueError(f"{name} is an integer quantity, not {value!r}")
    return str(int(value))


def _format_float(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    # -0.00 is the same value as 0.00; write it one way
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return text


def format_datetime(value, decimals: int) -> str:
    """A UTC datetime as `YYYY-MM-DDThh:mm:ss[.f…]Z` with `decimals` fractional-second digits,
    rounded half up. Takes a timezone-aware datetime (including a pandas Timestamp) or a
    numpy datetime64, which is UTC by convention.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"naive datetime {value!r}: times must be timezone-aware UTC")
        delta = value - _EPOCH
        # a pandas Timestamp keeps nanoseconds beyond datetime's microseconds
        ns = (
            (delta.days * 86_400 + delta.seconds) * 10**9
            + delta.microseconds * 1_000
            + getattr(value, "nanosecond", 0)
        )
    elif isinstance(value, np.datetime64):
        ns = value.astype("datetime64[ns]").astype(np.int64)
    else:
        raise TypeError(f"not a datetime: {value!r}")
    if decimals > 9:
        raise ValueError(f"at most 9 fractional-second digits, not {decimals}")
    step = 10 ** (9 - decimals)  # [ns]
    ns = (int(ns) + step // 2) // step * step
    seconds, fraction = divmod(ns, 10**9)
    text = np.datetime_as_string(np.datetime64(seconds, "s"), unit="s")
    if decimals:
        text += "." + f"{fraction:09d}"[:decimals]
    return text + "Z"


def receiver_decimals(texts) -> int:
    """The receiver's precision: the most digits after the decimal point among `texts`, the
    values as the receiver wrote them. Missing values and texts without a point count as 0.
    """
    return max((len(t.partition(".")[2]) for t in texts if t and "." in t), default=0)


# -- Nav record columns. Names are R2R's GeoCSV field_standard_names. --

ISO_TIME = Quantity("iso_time", "Date and time (UTC)", Datatype.DATETIME)
SHIP_LONGITUDE = Quantity(
    "ship_longitude", "Longitude of the vessel's GNSS antenna", Datatype.FLOAT, "degrees_east"
)
SHIP_LATITUDE = Quantity(
    "ship_latitude", "Latitude of the vessel's GNSS antenna", Datatype.FLOAT, "degrees_north"
)
NMEA_QUALITY = Quantity("nmea_quality", "NMEA GGA fix quality indicator", Datatype.INTEGER, "1")
NSV = Quantity("nsv", "Number of satellites used in the fix", Datatype.INTEGER, "1")
HDOP = Quantity("hdop", "Horizontal dilution of precision", Datatype.FLOAT, "1")
ANTENNA_HEIGHT = Quantity(
    "antenna_height", "Antenna height above mean sea level", Datatype.FLOAT, "m"
)
SPEED_MADE_GOOD = Quantity(
    "speed_made_good",
    "Speed over ground, from successive positions",
    Datatype.FLOAT,
    "m s-1",
    decimals=2,
)
COURSE_MADE_GOOD = Quantity(
    "course_made_good",
    "Course over ground, clockwise from true north",
    Datatype.FLOAT,
    "degree",
    decimals=3,
)

# -- Derived and summary quantities (QA statistics, XML results, bounds, infos, ranges) --

HORIZONTAL_ACCELERATION = Quantity(
    "horizontal_acceleration",
    "Horizontal acceleration, from successive speeds",
    Datatype.FLOAT,
    "m s-2",
    decimals=3,
)
PERCENTAGE = Quantity("percentage", "Percentage", Datatype.FLOAT, "percent", decimals=2)
DURATION = Quantity("duration", "Duration", Datatype.FLOAT, "s", decimals=3)
COUNT = Quantity("count", "Count", Datatype.INTEGER, "1")

QUANTITIES: dict[str, Quantity] = {
    q.name: q
    for q in (
        ISO_TIME,
        SHIP_LONGITUDE,
        SHIP_LATITUDE,
        NMEA_QUALITY,
        NSV,
        HDOP,
        ANTENNA_HEIGHT,
        SPEED_MADE_GOOD,
        COURSE_MADE_GOOD,
        HORIZONTAL_ACCELERATION,
        PERCENTAGE,
        DURATION,
        COUNT,
    )
}

# The column order of the nav products
BESTRES_COLUMNS = (
    ISO_TIME,
    SHIP_LONGITUDE,
    SHIP_LATITUDE,
    NMEA_QUALITY,
    NSV,
    HDOP,
    ANTENNA_HEIGHT,
    SPEED_MADE_GOOD,
    COURSE_MADE_GOOD,
)
ONE_MINUTE_COLUMNS = (ISO_TIME, SHIP_LONGITUDE, SHIP_LATITUDE, SPEED_MADE_GOOD, COURSE_MADE_GOOD)
CONTROL_COLUMNS = (ISO_TIME, SHIP_LONGITUDE, SHIP_LATITUDE)
