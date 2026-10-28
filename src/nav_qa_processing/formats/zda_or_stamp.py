"""GGA dated by ZDA, or by the logger's timestamp in a file without ZDA: the reader behind nav12.

nav12's lines are a device ID, the logger's time as `year:day-of-year:hh:mm:ss.ssss` and an NMEA
sentence (`doy_time`). Healy's POS/MV logged ZDA (HLY08TD); Langseth's C-Nav 3050 logged only
GGA (MGL1509, R2R alias `lds`). A file with a valid ZDA is read by `gga_zda`; a file without is
dated by its lines' timestamps, as `timestamped_gga` dates nav33. Consecutive files of one kind
are read together, so a ZDA still dates GGAs across a file boundary.

NavManager's `parse_nav12()` (`include/parsers/nav12.php`) does the same per file: ZDA when the
file has one, else the logger's date (the day before when the GGA's time is later than the
logger's). In that branch it also drops a GGA identical to the previous line's; here QC flags
the repeat (TIME_SEQUENCE).
"""

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import partial
from itertools import groupby

from nav_qa_processing.formats import gga_zda, timestamped_gga
from nav_qa_processing.formats.gga_zda import Fixes, Result

# A device ID, then the logger's year:day-of-year:hh:mm:ss.ssss, then whitespace
_DOY = re.compile(r"\s*\S+\s+(\d{4}):(\d{3}):(\d\d):(\d\d):(\d\d(?:\.\d*)?)\s")


def doy_time(line: str) -> tuple[date, int] | None:
    """A nav12 line's logger timestamp: (date, time of day [ns]), or None if it has none"""
    match = _DOY.match(line)
    if match is None:
        return None
    year, doy, hour, minute = (int(g) for g in match.groups()[:4])
    seconds = float(match.group(5))
    if not 1 <= doy <= 366 or hour > 23 or minute > 59 or seconds >= 61:
        return None
    day = date(year, 1, 1) + timedelta(days=doy - 1)
    if day.year != year:  # day 366 of a common year
        return None
    return day, (hour * 3600 + minute * 60) * 10**9 + round(seconds * 1e9)


_spans = gga_zda.SpanCache()  # whether a file has a valid ZDA, as its span found


def _has_zda(name: str, lines: list[str]) -> bool:
    kept = _spans.take(name, lines)
    return gga_zda.span(lines) is not None if kept is None else kept


def span(lines: list[str], name: str = "") -> tuple[datetime, datetime] | None:
    """A file's first and last ZDA, or without one its first and last logger timestamps"""
    zda_span = gga_zda.span(lines)
    _spans.keep(name, lines, zda_span is not None)
    return zda_span or timestamped_gga.span(lines, stamp=doy_time)


def read(files: Iterable[tuple[str, list[str]]]) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given, one file at a
    time
    """
    merged = Result(Fixes(), [], {}, {})
    for has_zda, run in groupby(files, key=lambda file: _has_zda(*file)):
        read = gga_zda.read if has_zda else partial(timestamped_gga.read, stamp=doy_time)
        part = read(run)
        merged.fixes += part.fixes
        merged.errors += part.errors
        merged.rejected_checksums |= part.rejected_checksums
        merged.skipped |= part.skipped
    _spans.clear()
    return merged
