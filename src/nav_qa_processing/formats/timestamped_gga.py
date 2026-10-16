"""GGA fixes dated by the logger's timestamp: the reader behind nav33 (OpenRVDAS, Falkor (too)).

Each line is an ISO 8601 UTC timestamp, a comma (or a space) and a GGA sentence:

    2012-04-28T00:00:01.109000Z,$GPGGA,000000.987,2635.03241,N,09331.21859,W,2,09,1.1,...

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

NavManager's nav33 parser (`include/parsers/nav33.php`) dates each GGA by its logger date, then
corrects the date across midnight buffer by buffer. Not reproduced (docs/navmanager-differences.md):
- it drops the 86,400th fix of each buffer (R8) and the cruise's last fix (R9): FKt230417, 18
  and 1;
- it drops a GGA repeating the previous one's time of day ("duplicate records caused by
  rounding seconds", for Falkor); here QC flags it (TIME_SEQUENCE);
- with its checksum test it drops the GGAs whose checksum is 0A–0F (R1): FKt230417, 48,427
  fixes, 2.9 %.
"""

import re
from datetime import UTC, date, datetime

from nav_qa_processing.formats.gga_zda import DatedFix, Result, date_from, sentences
from nav_qa_processing.formats.nmea import GGA, parse_sentence
from nav_qa_processing.readable import plural

# The logger timestamp starting a line, then a comma or a space
_TIMESTAMP = re.compile(r"\s*(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d(?:\.\d*)?)Z?[ ,]")


def logger_time(line: str) -> tuple[date, int] | None:
    """A line's logger timestamp: (date, time of day [ns]), or None if it has none"""
    match = _TIMESTAMP.match(line)
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups()[:5])
    if hour > 23 or minute > 59:
        return None
    try:
        day_ = date(year, month, day)
    except ValueError:
        return None
    seconds = float(match.group(6))
    if seconds >= 61:  # 60 is a leap second
        return None
    return day_, (hour * 3600 + minute * 60) * 10**9 + round(seconds * 1e9)


def span(lines: list[str]) -> tuple[datetime, datetime] | None:
    """The first and last logger timestamps in a file, to select and order files by"""
    times = []
    for line in lines:
        stamp = logger_time(line)
        if stamp is not None:
            day, ns = stamp
            times.append(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() + ns / 1e9)
    if not times:
        return None
    first, last = (datetime.fromtimestamp(t, UTC) for t in (times[0], times[-1]))
    return first, last


def read(files: list[tuple[str, list[str]]]) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given"""
    fixes: list[DatedFix] = []
    errors: list[str] = []
    rejected: dict[str, int] = {}
    for name, lines in files:
        timeless = unstamped = 0
        for number, text in sentences(lines):
            sentence = parse_sentence(text)
            if sentence is None:
                if "GGA" in text:  # only the sentence this reader uses
                    rejected[name] = rejected.get(name, 0) + 1
                continue
            if sentence.kind != "GGA":
                continue
            gga = GGA.from_fields(sentence.fields)
            if gga.time is None:
                timeless += 1
                continue
            stamp = logger_time(lines[number - 1])
            if stamp is None:
                unstamped += 1
                continue
            fix = DatedFix(gga, name, number)
            fix.day = date_from(*stamp, gga.time.ns)
            fixes.append(fix)
        if timeless:
            errors.append(f"{name}: {plural(timeless, 'GGA')} without a time, skipped")
        if unstamped:
            errors.append(
                f"{name}: {plural(unstamped, 'GGA')} without a logger timestamp, skipped"
            )
    return Result(fixes, errors, rejected)
