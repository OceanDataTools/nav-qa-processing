"""NMEA 0183 sentence parsing: checksum, GGA and ZDA.

Behavior ported from NavManager's nmeatools (observed, not copied):
- A line can hold a partial sentence followed by a complete one after a stream interruption.
  Only the text from the last `$` is used.
- A sentence without a checksum is accepted if it has every field of its kind but optional
  trailing ones (`FIELDS`), so a sentence cut short (e.g. by a logger splitting it across
  lines) isn't read as a short one.
  A sentence with a checksum is accepted only when it matches, compared by value: `*0A`, `*0a`
  and a nonstandard one-digit `*A` are all 0x0A. NavManager compares unpadded hex with PHP's
  loose `==`, so it rejects every correct checksum from `*0A` to `*0F` (`*00`–`*09` compare as
  numbers and pass); that isn't reproduced (#13, register R1). NavManager also reads truncated sentences, whose missing fields become 0 (RR2406).
- A coordinate needs its hemisphere letter (N/S, E/W): without one it's missing, not north or
  east. NavManager reads it as positive, so RR2406's truncated GGAs put the ship at 83°E.
- Positions keep the receiver's precision: a coordinate with n decimal minutes is reported
  with n + 2 decimal degrees (`lat_decimals`, `lon_decimals`). Times keep their decimals too.
- Empty numeric GGA fields parse as missing here. NavManager turns them into 0, which QC
  then flags. That mapping belongs to the r2rnav parity layer, not the parser.
"""

from dataclasses import dataclass

# The number of fields, address included, of a complete sentence: one without a checksum and
# with fewer has been cut short. A GGA's last two (DGPS age and station) are optional: nav1's
# documented sample, without a checksum, ends at the geoid separation's unit (#81)
FIELDS = {"GGA": 13, "ZDA": 7}


@dataclass(frozen=True)
class Sentence:
    """One NMEA sentence split into fields. `fields[0]` is the address, e.g. `$GPGGA`."""

    fields: list[str]
    checksum: str | None  # as supplied, or None

    @property
    def kind(self) -> str:
        """The sentence formatter: `GGA`, `ZDA`, …, or the whole address for `$P…` sentences"""
        address = self.fields[0].lstrip("$")
        return address if address.startswith("P") else address[2:]


def checksum(body: str) -> int:
    """XOR of every character of `body`, the text between `$` and `*`"""
    value = 0
    for char in body:
        value ^= ord(char)
    return value


def parse_sentence(line: str) -> Sentence | None:
    """The last sentence on `line`, or None if there is none or its checksum is wrong"""
    start = line.rfind("$")
    if start < 0:
        return None
    text = line[start:].strip()
    data, star, supplied = text.partition("*")
    if star:
        computed = checksum(data[1:])
        supplied = supplied.strip().lower()
        if not _is_hex(supplied) or int(supplied, 16) != computed:
            return None
        return Sentence(data.split(","), supplied)
    sentence = Sentence(data.split(","), None)
    if len(sentence.fields) < FIELDS.get(sentence.kind, 0):
        return None
    return sentence


def has_checksum(text: str) -> bool:
    """Whether a sentence's text ends in a complete checksum (`*` and two hex digits)"""
    _, star, supplied = text.strip().rpartition("*")
    return bool(star) and len(supplied) == 2 and _is_hex(supplied.lower())


def _is_hex(text: str) -> bool:
    return 1 <= len(text) <= 2 and all(c in "0123456789abcdef" for c in text)


def decimals(text: str) -> int:
    """Digits after the decimal point in `text`"""
    return len(text.partition(".")[2])


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class TimeOfDay:
    """`hhmmss[.s…]` as nanoseconds since midnight, with the receiver's decimals"""

    ns: int  # [ns]
    decimals: int

    @property
    def seconds(self) -> float:  # [s]
        return self.ns / 1e9

    @classmethod
    def parse(cls, text: str) -> "TimeOfDay | None":
        whole, _, fraction = text.strip().partition(".")
        if not whole.isdigit() or (fraction and not fraction.isdigit()):
            return None
        hh, rest = divmod(int(whole), 10_000)
        mm, ss = divmod(rest, 100)
        ns = int((fraction + "0" * 9)[:9]) if fraction else 0
        return cls((hh * 3600 + mm * 60 + ss) * 10**9 + ns, len(fraction))


def _degrees(text: str, hemisphere: str, negative: str, positive: str) -> float | None:
    value = _float(text)
    if value is None or hemisphere not in (negative, positive):
        return None
    deg, minutes = divmod(value, 100)
    degrees = deg + minutes / 60
    return -degrees if hemisphere == negative else degrees


# What a GGA's talker ID says about the device (NMEA 0183)
TALKERS = {
    "GP": "GPS",
    "GL": "GLONASS",
    "GA": "Galileo",
    "GB": "BeiDou",
    "BD": "BeiDou",
    "GQ": "QZSS",
    "GI": "NavIC",
    "GN": "multi-constellation GNSS",
    "IN": "integrated navigation (INS)",
}


@dataclass(frozen=True)
class GGA:
    """GPS fix data. Missing fields are None."""

    time: TimeOfDay | None
    lat: float | None  # [degrees_north]
    lon: float | None  # [degrees_east]
    lat_decimals: int
    lon_decimals: int
    quality: int | None
    nsat: int | None
    hdop: float | None
    hdop_decimals: int
    alt: float | None  # [m]
    alt_decimals: int
    talker: str = ""  # the sentence's talker ID, e.g. GP (GPS), GN (GNSS), IN (integrated)

    @classmethod
    def from_fields(cls, f: list[str]) -> "GGA":
        f = f + [""] * (10 - len(f))
        return cls(
            time=TimeOfDay.parse(f[1]),
            lat=_degrees(f[2], f[3], "S", "N"),
            lon=_degrees(f[4], f[5], "W", "E"),
            lat_decimals=decimals(f[2]) + 2,
            lon_decimals=decimals(f[4]) + 2,
            quality=_int(f[6]),
            nsat=_int(f[7]),
            hdop=_float(f[8]),
            hdop_decimals=decimals(f[8]),
            alt=_float(f[9]),
            alt_decimals=decimals(f[9]),
            talker=f[0].lstrip("$!")[:-3],
        )


@dataclass(frozen=True)
class ZDA:
    """Date and time (UTC)"""

    time: TimeOfDay | None
    day: int | None
    month: int | None
    year: int | None

    @classmethod
    def from_fields(cls, f: list[str]) -> "ZDA":
        f = f + [""] * (5 - len(f))
        return cls(TimeOfDay.parse(f[1]), _int(f[2]), _int(f[3]), _int(f[4]))
