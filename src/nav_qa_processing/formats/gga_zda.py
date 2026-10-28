"""GGA fixes dated by ZDA sentences: the reader behind nav29 (and the other GGA + ZDA formats).

A GGA carries only the time of day. Each GGA is dated from one of the ZDAs either side of it in
the stream of sentences (across files, in the order given): the one closer to it in time of day,
counting across midnight (a tie goes to the following ZDA). If the two times of day are more
than 12 hours apart, they straddle midnight, and the date moves a day: back when the GGA's time
is the later one, forward when it's the earlier. Comparing times rather than positions in the
stream matters at a gap: the first GGA of a file that starts 13.5 h after the previous file
ended is next to the previous file's last ZDA, but belongs with the ZDA right after it (SP2503).

- Any line prefix (a logger timestamp) is ignored. The text from the last `$` is the sentence.
- A logger can split a sentence across lines, each with its own timestamp (RR2406: 40 % of its
  GGAs, cut anywhere up to the checksum's last digit). A sentence without a complete checksum
  is joined with the text of the following lines (after their timestamps, up to any `$`) until
  it has one, and kept only if the checksum matches. NavManager reads the first part as a
  sentence of its own, with the missing fields as 0, and drops the rest.
- Every GGA with a parseable time is kept, including repeated or out-of-order times: QC decides
  what is a duplicate or out of sequence.

NavManager dates GGAs buffer by buffer, which drops records and misdates some. None of it is
reproduced (`CLAUDE.md`, "What parity with R2R means"):
- it drops the last GGA after the final ZDA of the last file (its final loop stops one short):
  R2R's SP2424 bestres lacks its final fix, 2024-11-04T03:16:44.50Z (#14);
- it drops a GGA whose time of day equals that of the last GGA it dated, and a GGA at exactly
  00:00:00 before anything is dated (#14);
- after the GGAs a ZDA dates, an equal time of day advances the date by a day (#15).

Recorded in `errors`, not raised, counted per file:
- a GGA without a parseable time and a ZDA without a valid date are skipped (SP2220's receiver
  logged 695,387 empty GGAs while it had no fix);
- with no valid ZDA at all, no GGA can be dated, and none is returned.
"""

import math
import re
from array import array
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Self

import numpy as np
import pandas as pd

from nav_qa_processing.formats.nmea import (
    GGA,
    GLL,
    ZDA,
    TimeOfDay,
    has_checksum,
    parse_sentence,
    unquoted,
)
from nav_qa_processing.readable import plural

_DAY = 86_400 * 10**9  # [ns]
_HALF_DAY = _DAY // 2
_EPOCH = date(1970, 1, 1)
# A logger timestamp starting a line: Unix seconds, ISO 8601 or SCS's MM/DD/YYYY,hh:mm:ss.sss,
# then a space or a comma
_TIMESTAMP = re.compile(
    r"\s*(\d+(\.\d*)?|\d{4}-\d\d-\d\dT[\d:.]+Z?|\d\d/\d\d/\d{4},\d\d:\d\d:[\d.]+)[ ,]"
)
# A split sentence is joined with at most this many following lines
MAX_CONTINUATIONS = 3
# ZDA years taken as dates: the GPS era. Others are corrupt (e.g. a truncated year)
YEARS = range(1980, 2101)


@dataclass(slots=True)
class DatedFix:
    gga: GGA
    source_file: str
    line: int
    day: date | None = None

    @property
    def time_ns(self) -> int:
        """Nanoseconds since 1970-01-01T00:00:00Z"""
        return (self.day - _EPOCH).days * _DAY + self.gga.time.ns

    @property
    def moment(self) -> datetime:
        """The fix's UTC date and time, to the microsecond"""
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=self.time_ns // 1000)


# Fixes' columns: array type code. Missing values are NaN, so quality and nsat are floats to
# allow them. `day` is the date's ordinal, 0 undated; `talker` and `file` index Fixes' lists
FIX_COLUMNS = {
    "time_ns": "q",  # time of day [ns]
    "time_decimals": "i",
    "day": "i",
    "lat": "d",
    "lon": "d",
    "lat_decimals": "i",
    "lon_decimals": "i",
    "quality": "d",
    "nsat": "d",
    "hdop": "d",
    "hdop_decimals": "i",
    "alt": "d",
    "alt_decimals": "i",
    "talker": "i",
    "file": "i",
    "line": "i",
}
_DTYPES = {"q": np.int64, "i": np.int32, "d": np.float64}


class Fixes:
    """A reader's dated fixes as columns (#157). Each fix's values are copied in and its GGA
    dropped, so a fileset's fixes take about 90 bytes each, not about 660 as objects (a 20-day
    cruise at 2 Hz, 3.5 M fixes, ran a 4 GB OpenVDM server out of memory). Iterating and
    indexing give DatedFixes; `column` gives a column as a numpy array
    """

    def __init__(self):
        self._columns = {name: array(code) for name, code in FIX_COLUMNS.items()}
        self._appends = tuple(column.append for column in self._columns.values())
        self.files: list[str] = []  # source files, in order of their first fix
        self.talkers: list[str] = []
        self._indexes: dict[int, dict[str, int]] = {id(self.files): {}, id(self.talkers): {}}

    def _index(self, names: list[str], name: str) -> int:
        indexes = self._indexes[id(names)]
        if name not in indexes:
            indexes[name] = len(names)
            names.append(name)
        return indexes[name]

    def append(self, gga: GGA, source_file: str, line: int, day: date | None = None) -> None:
        """A fix with a time of day, as a DatedFix would hold it"""
        values = (
            gga.time.ns,
            gga.time.decimals,
            day.toordinal() if day else 0,
            _or_nan(gga.lat),
            _or_nan(gga.lon),
            gga.lat_decimals,
            gga.lon_decimals,
            _or_nan(gga.quality),
            _or_nan(gga.nsat),
            _or_nan(gga.hdop),
            gga.hdop_decimals,
            _or_nan(gga.alt),
            gga.alt_decimals,
            self._index(self.talkers, gga.talker),
            self._index(self.files, source_file),
            line,
        )
        for append, value in zip(self._appends, values, strict=True):
            append(value)

    def extend(self, other: "Fixes") -> None:
        """other's fixes after these"""
        remap = {
            "file": np.array([self._index(self.files, n) for n in other.files], np.int32),
            "talker": np.array([self._index(self.talkers, t) for t in other.talkers], np.int32),
        }
        for name, column in self._columns.items():
            if name in remap:
                column.frombytes(remap[name][other.column(name)].tobytes())
            else:
                column.extend(other._columns[name])

    def __iadd__(self, other: "Fixes") -> Self:
        self.extend(other)
        return self

    def set_days(self, days: np.ndarray) -> None:
        """Every fix's date, as ordinals"""
        self._columns["day"][:] = array("i", np.asarray(days, np.int32).tobytes())

    def column(self, name: str) -> np.ndarray:
        """A copy of a column (a view would stop the column growing)"""
        column = self._columns[name]
        return np.frombuffer(column, _DTYPES[column.typecode]).copy()

    def drain(self) -> dict[str, np.ndarray]:
        """Every column as a numpy array over the same memory, not a copy, with `day` and
        `time_ns` made `time` [ns since 1970-01-01T00:00:00Z], leaving no fixes: the fixes
        handed on without holding them twice
        """
        columns = {
            name: np.frombuffer(column, _DTYPES[column.typecode])
            for name, column in self._columns.items()
        }
        self.__init__()
        days = columns.pop("day").astype(np.int64) - _EPOCH.toordinal()
        columns["time"] = days * _DAY + columns.pop("time_ns")
        return columns

    def __len__(self) -> int:
        return len(self._columns["line"])

    def __getitem__(self, index: int) -> "DatedFix":
        if not -len(self) <= index < len(self):
            raise IndexError(index)
        value = {name: column[index] for name, column in self._columns.items()}
        missing = lambda v: None if math.isnan(v) else v
        whole = lambda v: None if math.isnan(v) else int(v)
        gga = GGA(
            time=TimeOfDay(value["time_ns"], value["time_decimals"]),
            lat=missing(value["lat"]),
            lon=missing(value["lon"]),
            lat_decimals=value["lat_decimals"],
            lon_decimals=value["lon_decimals"],
            quality=whole(value["quality"]),
            nsat=whole(value["nsat"]),
            hdop=missing(value["hdop"]),
            hdop_decimals=value["hdop_decimals"],
            alt=missing(value["alt"]),
            alt_decimals=value["alt_decimals"],
            talker=self.talkers[value["talker"]],
        )
        day = date.fromordinal(value["day"]) if value["day"] else None
        return DatedFix(gga, self.files[value["file"]], value["line"], day)

    def __iter__(self) -> Iterator["DatedFix"]:
        return (self[i] for i in range(len(self)))

    def __eq__(self, other) -> bool:
        if isinstance(other, Fixes | list):
            return list(self) == list(other)
        return NotImplemented

    __hash__ = None


def _or_nan(value: float | None) -> float:
    return math.nan if value is None else value


class SpanCache:
    """What a reader's `span` worked out for a file, kept for its `read` to reuse rather than
    parse the file again (code review, 2026-10-18). `rawnav` reads each file again for `read`
    rather than keep every file's lines (#157), so it's keyed by the file's name, length and
    first and last lines: a file of the same name elsewhere, or one a logger added to since,
    is parsed again. `read` clears it, so files `span` saw but `read` skipped don't linger
    """

    def __init__(self):
        self._kept: dict[tuple, object] = {}

    @staticmethod
    def _key(name: str, lines: list[str]) -> tuple:
        return (name, len(lines), lines[0] if lines else "", lines[-1] if lines else "")

    def keep(self, name: str, lines: list[str], value: object) -> None:
        self._kept[self._key(name, lines)] = value

    def take(self, name: str, lines: list[str]) -> object | None:
        return self._kept.pop(self._key(name, lines), None)

    def clear(self) -> None:
        self._kept.clear()


# Why a reader skipped a sentence, as Result.skipped counts them (#67)
NO_TIME = "GGA without a time"
NO_VALID_DATE = "ZDA without a valid date"
NO_LOGGER_TIMESTAMP = "GGA without a logger timestamp"
NO_ZDA_TO_DATE_IT = "GGA with no ZDA in any file to date it"
FAILED_CHECKSUM = "failed its checksum"
CUT_SHORT = "cut short (no checksum, too few fields)"


def has_star(text: str) -> bool:
    """Whether a sentence that couldn't be read had a checksum (so it failed it), or was cut
    short without one (#99 review)
    """
    return "*" in text


def report_cut_short(errors: list[str], skipped: dict, name: str, count: int) -> None:
    if count:
        errors.append(f"{name}: {plural(count, 'sentence')} cut short, skipped")
    count_skipped(skipped, name, CUT_SHORT, count)


@dataclass
class Result:
    fixes: Fixes
    errors: list[str]  # "<file>: <message>"
    rejected_checksums: dict[str, int]  # GGA and ZDA sentences, per file
    # Sentences skipped, per file and reason (the reasons above), checksums included
    skipped: dict[str, dict[str, int]] = field(default_factory=dict)


def count_skipped(skipped: dict[str, dict[str, int]], name: str, reason: str, count: int) -> None:
    if count:
        reasons = skipped.setdefault(name, {})
        reasons[reason] = reasons.get(reason, 0) + count


def _zda_day(zda: ZDA) -> date | None:
    if zda.year not in YEARS:
        return None
    try:
        return date(zda.year, zda.month, zda.day)
    except (TypeError, ValueError):
        return None


def _apart(a_ns: np.ndarray, b_ns: np.ndarray) -> np.ndarray:
    """How far apart times of day are [ns], across midnight"""
    difference = np.abs(a_ns - b_ns) % _DAY
    return np.minimum(difference, _DAY - difference)


def date_from(zda_day: date, zda_ns: int, gga_ns: int) -> date:
    """The date of a GGA at time of day `gga_ns`, from a nearby ZDA's date and time of day"""
    difference = gga_ns - zda_ns
    if difference > _HALF_DAY:  # GGA before midnight, ZDA after it
        return zda_day - timedelta(days=1)
    if difference < -_HALF_DAY:  # GGA after midnight, ZDA before it
        return zda_day + timedelta(days=1)
    return zda_day


def _continuation(line: str) -> str | None:
    """A line's text after its logger timestamp, up to any `$`, or None if a sentence starts it"""
    match = _TIMESTAMP.match(line)
    text = line[match.end() :] if match else line
    text = text.split("$", 1)[0] if "$" in text else text
    return None if not text.strip() and "$" in line else text.rstrip("\r\n")


def sentences(lines: list[str]) -> Iterator[tuple[int, str]]:
    """(line number, sentence text) for each line with a `$`; a sentence split across lines is
    joined with its continuations until it has a checksum
    """
    for index, line in enumerate(lines):
        start = line.rfind("$")
        if start < 0:
            continue
        text = unquoted(line[start:])
        following = index + 1
        while (
            not has_checksum(text)
            and following < len(lines)
            and following - index <= MAX_CONTINUATIONS
        ):
            continuation = _continuation(lines[following])
            if continuation is None:
                break
            text += continuation
            if "$" in lines[following]:
                break  # a new sentence follows on that line
            following += 1
        yield index + 1, text


def span(lines: list[str]) -> tuple[datetime, datetime] | None:
    """The first and last valid ZDA times in a file, to select and order files by. Valid as
    `read` takes them: a year in YEARS and a real date (#99 review)
    """
    times = []
    for line in lines:
        if "ZDA" not in line:
            continue
        sentence = parse_sentence(line)
        if sentence is None or sentence.kind != "ZDA":
            continue
        zda = ZDA.from_fields(sentence.fields)
        zda_day = _zda_day(zda)
        if zda_day is None or zda.time is None:
            continue
        day = datetime(zda_day.year, zda_day.month, zda_day.day, tzinfo=UTC)
        times.append(day + pd.Timedelta(zda.time.ns, "ns").to_pytimedelta())
    return (times[0], times[-1]) if times else None


def read(files: Iterable[tuple[str, list[str]]], kind: str = "GGA") -> Result:
    """Dated fixes from `files`, each (name, lines), in the order given: from GGA sentences, or
    with `kind` GLL from GLL (nav27, without satellites, HDOP or altitude). `files` is read
    once, one file at a time
    """
    reason = lambda text: text.replace("GGA", kind)
    fixes = Fixes()
    fix_positions = array("q")
    zda_days = array("i")  # ordinals
    zda_times = array("q")  # time of day [ns]
    zda_positions = array("q")
    errors: list[str] = []
    rejected: dict[str, int] = {}
    skipped: dict[str, dict[str, int]] = {}
    position = 0  # sentence count across files
    for name, lines in files:
        timeless = undated = cut = 0
        for number, line in sentences(lines):
            sentence = parse_sentence(line)
            if sentence is None:
                if kind in line or "ZDA" in line:  # only the sentences this reader uses
                    if has_star(line):
                        rejected[name] = rejected.get(name, 0) + 1
                    else:
                        cut += 1
                continue
            position += 1
            match sentence.kind:
                case "GGA" | "GLL" if sentence.kind == kind:
                    gga = (
                        GGA.from_fields(sentence.fields)
                        if kind == "GGA"
                        else GLL.from_fields(sentence.fields).as_gga()
                    )
                    if gga.time is None:
                        timeless += 1
                        continue
                    fixes.append(gga, name, number)
                    fix_positions.append(position)
                case "ZDA":
                    zda = ZDA.from_fields(sentence.fields)
                    day = _zda_day(zda)
                    if day is None or zda.time is None:
                        undated += 1
                        continue
                    zda_days.append(day.toordinal())
                    zda_times.append(zda.time.ns)
                    zda_positions.append(position)
        if timeless:
            errors.append(f"{name}: {plural(timeless, kind)} without a time, skipped")
        if undated:
            errors.append(f"{name}: {plural(undated, 'ZDA')} without a valid date, skipped")
        count_skipped(skipped, name, reason(NO_TIME), timeless)
        count_skipped(skipped, name, NO_VALID_DATE, undated)
        count_skipped(skipped, name, FAILED_CHECKSUM, rejected.get(name, 0))
        report_cut_short(errors, skipped, name, cut)

    if not zda_positions:
        if fixes:
            errors.append(f"no ZDA in any file: {len(fixes)} {kind} fixes can't be dated")
        per_file = np.bincount(fixes.column("file"), minlength=len(fixes.files))
        for name, count in zip(fixes.files, per_file, strict=True):
            count_skipped(skipped, name, reason(NO_ZDA_TO_DATE_IT), int(count))
        return Result(Fixes(), errors, rejected, skipped)
    fixes.set_days(
        _nearest_zda_days(
            np.frombuffer(fix_positions, np.int64),
            fixes.column("time_ns"),
            np.frombuffer(zda_positions, np.int64),
            np.frombuffer(zda_days, np.int32).astype(np.int64),
            np.frombuffer(zda_times, np.int64),
        )
    )
    return Result(fixes, errors, rejected, skipped)


def _nearest_zda_days(
    fix_positions: np.ndarray,
    fix_times: np.ndarray,
    zda_positions: np.ndarray,
    zda_days: np.ndarray,
    zda_times: np.ndarray,
) -> np.ndarray:
    """Each fix's date (ordinal) from the ZDA either side of it in the stream closer in time of
    day, across midnight, a tie going to the following ZDA; dated as `date_from` does
    """
    following = np.searchsorted(zda_positions, fix_positions, side="left")
    after = np.minimum(following, len(zda_positions) - 1)
    before = np.maximum(following - 1, 0)
    never = np.iinfo(np.int64).max
    gap_after = np.where(
        following < len(zda_positions), _apart(zda_times[after], fix_times), never
    )
    gap_before = np.where(following > 0, _apart(zda_times[before], fix_times), never)
    nearest = np.where(gap_before < gap_after, before, after)
    difference = fix_times - zda_times[nearest]
    return zda_days[nearest] - (difference > _HALF_DAY) + (difference < -_HALF_DAY)
