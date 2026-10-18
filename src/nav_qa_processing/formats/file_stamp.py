"""GGA fixes dated from the file name: the reader behind nav22 (bare NMEA with no date).

nav22's files (Revelle's and New Horizon's Ashtech ADU2: PASHR ATT, GGA, VTG) carry no date in
any sentence; the file name's `YYYYMMDDhhmmss` stamp gives it (`ashtech_20120918225733.raw`,
`name_time`). A file's first GGA takes the date that puts it nearest the name's time, and each
GGA after it the date that puts it nearest the fix before (within 12 hours), so a file that
runs past midnight moves to the next day there.

NavManager's nav22 case (`navcopy.inc.php`) takes the stamp after the first `_` of the file's
whole path, gives every GGA of the file that date, then carries the first one's date through
each 86,400-fix buffer, adding a day where the time of day falls by more than 10 hours. Here:
- the stamp is found in the file name alone, so an `_` in a directory name doesn't break it;
- each file starts from its own stamp, not from the buffer's first date.

Recorded in `errors`, not raised: a file without a stamp in its name can't be dated and is
skipped (`rawnav` reports it unread); a GGA without a parseable time is skipped.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePath

from nav_qa_processing.formats.gga_zda import (
    FAILED_CHECKSUM,
    NO_TIME,
    DatedFix,
    Result,
    count_skipped,
    date_from,
    sentences,
)
from nav_qa_processing.formats.nmea import GGA, parse_sentence
from nav_qa_processing.readable import plural

# YYYYMMDDhhmmss in a file name, optionally with a T, _ or - between date and time
_NAME_STAMP = re.compile(r"(\d{4})(\d\d)(\d\d)[T_-]?(\d\d)(\d\d)(\d\d)")
NO_NAME_STAMP = "GGA in a file whose name has no date"


def name_time(name: str) -> tuple[date, int] | None:
    """The date and time of day [ns] in a file name's `YYYYMMDDhhmmss`, or None"""
    for match in _NAME_STAMP.finditer(PurePath(name).name):
        year, month, day, hour, minute, second = (int(g) for g in match.groups())
        if hour > 23 or minute > 59 or second > 60:
            continue
        try:
            return date(year, month, day), (hour * 3600 + minute * 60 + second) * 10**9
        except ValueError:
            continue
    return None


def _dated(name: str, lines: list[str]) -> tuple[list[DatedFix], int, int]:
    """A file's fixes dated from its name's stamp, the GGAs without a time, and the GGAs that
    failed their checksum
    """
    stamp = name_time(name)
    fixes: list[DatedFix] = []
    timeless = rejected = 0
    for number, text in sentences(lines):
        sentence = parse_sentence(text)
        if sentence is None:
            rejected += "GGA" in text
            continue
        if sentence.kind != "GGA":
            continue
        gga = GGA.from_fields(sentence.fields)
        if gga.time is None:
            timeless += 1
            continue
        fix = DatedFix(gga, name, number)
        if stamp is not None:
            fix.day = date_from(*stamp, gga.time.ns)
            stamp = (fix.day, gga.time.ns)
        fixes.append(fix)
    return fixes, timeless, rejected


def span(lines: list[str], name: str = "") -> tuple[datetime, datetime] | None:
    """The first and last fix of a file, dated from its name, to select and order files by"""
    if name_time(name) is None:
        return None
    fixes, _, _ = _dated(name, lines)
    if not fixes:
        return None
    moment = lambda fix: (
        datetime(fix.day.year, fix.day.month, fix.day.day, tzinfo=UTC)
        + (timedelta(microseconds=fix.gga.time.ns // 1000))
    )
    return moment(fixes[0]), moment(fixes[-1])


def read(files: list[tuple[str, list[str]]]) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given"""
    result = Result([], [], {}, {})
    for name, lines in files:
        fixes, timeless, rejected = _dated(name, lines)
        if name_time(name) is None:
            if fixes:
                result.errors.append(
                    f"{name}: no date in the file name: {plural(len(fixes), 'GGA')} can't be "
                    "dated, skipped"
                )
            count_skipped(result.skipped, name, NO_NAME_STAMP, len(fixes))
            fixes = []
        result.fixes += fixes
        if timeless:
            result.errors.append(f"{name}: {plural(timeless, 'GGA')} without a time, skipped")
        if rejected:
            result.rejected_checksums[name] = rejected
        count_skipped(result.skipped, name, NO_TIME, timeless)
        count_skipped(result.skipped, name, FAILED_CHECKSUM, rejected)
    return result
