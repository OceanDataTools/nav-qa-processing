"""GGA fixes dated by the logger's timestamp: the reader behind nav33 (OpenRVDAS, Falkor (too))
and nav02 (NOAA SCS).

Each line is the logger's UTC timestamp, a comma (or a space) and a GGA sentence. nav33's is ISO
8601 (`iso_time`), nav02's the date and time as two fields (`scs_time`, #81):

    2012-04-28T00:00:01.109000Z,$GPGGA,000000.987,2635.03241,N,09331.21859,W,2,09,1.1,...
    08/19/2025,12:37:31.377,$GPGGA,123730,4129.5473,N,07125.1176,W,2,10,1.5,15.9,M,,M,,*7A

There's no ZDA. A GGA carries only its time of day, so it takes the date that puts it nearest
its own line's timestamp, counting across midnight: a GGA for 23:59:59.9 logged at
00:00:00.1 the next day is dated the day before. The time of the fix is the GGA's, not the
logger's, which differs from it by a fraction of a second (−0.09 to +0.25 s, median +0.06 s,
in OpenVDM's sample cruise).

As in `gga_zda`: a sentence split across lines is joined with its continuations (the first
line's timestamp dates it); every GGA with a parseable time is kept, repeats and all, for QC to
judge. Recorded in `errors`, not raised, counted per file: a GGA without a parseable time, and
one whose line has no logger timestamp, are skipped. A line without a sentence, such as
Falkor (too)'s CSV header (`Timestamp,Header,GPSTime,…`), is ignored.

NavManager's nav33 and nav2 parsers (`include/parsers/nav33.php`, `nav2.php`) date each GGA by
its logger date, then correct the date across midnight buffer by buffer. Not reproduced
(docs/navmanager-differences.md):
- it drops the 86,400th fix of each buffer (R8) and the cruise's last fix (R9): FKt230417, 18
  and 1; EN735 (nav02), 3 and 1;
- it drops the earlier of two GGAs with the same time of day ("duplicate records caused by
  rounding seconds", for Falkor; R10: EN735, 5); here both are kept and QC flags the later
  (TIME_SEQUENCE);
- with its checksum test it drops the GGAs whose checksum is 0A–0F (R1): FKt230417, 48,427
  fixes, 2.9 %.
"""

import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime

from nav_qa_processing.formats.gga_zda import (
    FAILED_CHECKSUM,
    NO_LOGGER_TIMESTAMP,
    NO_TIME,
    Fixes,
    Result,
    count_skipped,
    date_from,
    has_star,
    report_cut_short,
    sentences,
)
from nav_qa_processing.formats.nmea import GGA, RMC, parse_sentence
from nav_qa_processing.readable import plural

# The logger timestamp starting a line, then a comma or a space: ISO 8601 (nav33), and SCS's
# MM/DD/YYYY,hh:mm:ss.sss (nav02). Groups: year, month, day, hour, minute, seconds
_ISO = re.compile(r"\s*(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d(?:\.\d*)?)Z?[ ,]")
# OSU's DAS (nav15): DATA, then the ISO 8601 timestamp, then the quoted sentence
_OSU = re.compile(r"\s*DATA\s*,\s*(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d(?:\.\d*)?)Z?\s*,")
_SCS = re.compile(r"\s*(\d\d)/(\d\d)/(\d{4}),(\d\d):(\d\d):(\d\d(?:\.\d*)?),")

Stamp = Callable[[str], "tuple[date, int] | None"]


def iso_time(line: str) -> tuple[date, int] | None:
    """A line's ISO 8601 logger timestamp: (date, time of day [ns]), or None if it has none"""
    match = _ISO.match(line)
    return None if match is None else _stamp(*match.groups())


def osu_time(line: str) -> tuple[date, int] | None:
    """A nav15 line's logger timestamp (`DATA, 2011-04-11T00:00:00.158Z, "$GPGGA,…"`), like
    `iso_time`
    """
    match = _OSU.match(line)
    return None if match is None else _stamp(*match.groups())


def scs_time(line: str) -> tuple[date, int] | None:
    """A line's SCS logger date and time (`MM/DD/YYYY,hh:mm:ss.sss,`), like `iso_time`"""
    match = _SCS.match(line)
    if match is None:
        return None
    month, day, year, *clock = match.groups()
    return _stamp(year, month, day, *clock)


def _stamp(year, month, day, hour, minute, seconds) -> tuple[date, int] | None:
    year, month, day, hour, minute = (int(g) for g in (year, month, day, hour, minute))
    if hour > 23 or minute > 59:
        return None
    try:
        day_ = date(year, month, day)
    except ValueError:
        return None
    seconds = float(seconds)
    if seconds >= 61:  # 60 is a leap second
        return None
    return day_, (hour * 3600 + minute * 60) * 10**9 + round(seconds * 1e9)


def span(lines: list[str], stamp: Stamp = iso_time) -> tuple[datetime, datetime] | None:
    """The first and last logger timestamps in a file, to select and order files by"""
    times = []
    for line in lines:
        found = stamp(line)
        if found is not None:
            day, ns = found
            times.append(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() + ns / 1e9)
    if not times:
        return None
    first, last = (datetime.fromtimestamp(t, UTC) for t in (times[0], times[-1]))
    return first, last


def read(
    files: Iterable[tuple[str, list[str]]], stamp: Stamp = iso_time, kind: str = "GGA"
) -> Result:
    """Dated fixes from `files`, each (name, lines), in the order given: from GGA sentences, or
    with `kind` RMC from RMC (nav32, without satellites, HDOP or altitude). An RMC is dated by
    its logger timestamp too; RMCs whose own date differs are reported (7168 days: the GPS
    week-number rollover)
    """
    reason = lambda text: text.replace("GGA", kind)
    fixes = Fixes()
    errors: list[str] = []
    rejected: dict[str, int] = {}
    skipped: dict[str, dict[str, int]] = {}
    for name, lines in files:
        timeless = unstamped = cut = 0
        misdated: dict[int, int] = {}  # RMC date minus the fix's [days] -> RMCs
        for number, text in sentences(lines):
            sentence = parse_sentence(text)
            if sentence is None:
                if kind in text:  # only the sentence this reader uses
                    if has_star(text):
                        rejected[name] = rejected.get(name, 0) + 1
                    else:
                        cut += 1
                continue
            if sentence.kind != kind:
                continue
            rmc = RMC.from_fields(sentence.fields) if kind == "RMC" else None
            gga = rmc.as_gga() if rmc else GGA.from_fields(sentence.fields)
            if gga.time is None:
                timeless += 1
                continue
            logged = stamp(lines[number - 1])
            if logged is None:
                unstamped += 1
                continue
            day = date_from(*logged, gga.time.ns)
            fixes.append(gga, name, number, day)
            if rmc and rmc.day is not None and rmc.day != day:
                days = (rmc.day - day).days
                misdated[days] = misdated.get(days, 0) + 1
        if timeless:
            errors.append(f"{name}: {plural(timeless, kind)} without a time, skipped")
        if unstamped:
            errors.append(f"{name}: {plural(unstamped, kind)} without a logger timestamp, skipped")
        for days, count in sorted(misdated.items()):
            rollover = " (the GPS week-number rollover)" if abs(days) % 7168 == 0 else ""
            errors.append(
                f"{name}: {plural(count, 'RMC')} dated {days:+d} days from the logger"
                f"{rollover}; dated by the logger"
            )
        count_skipped(skipped, name, reason(NO_TIME), timeless)
        count_skipped(skipped, name, reason(NO_LOGGER_TIMESTAMP), unstamped)
        count_skipped(skipped, name, FAILED_CHECKSUM, rejected.get(name, 0))
        report_cut_short(errors, skipped, name, cut)
    return Result(fixes, errors, rejected, skipped)
