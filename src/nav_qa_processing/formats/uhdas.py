"""GGA dated by UHDAS's clock sentences: the readers behind nav31 (PYRTM) and uhdas (UNIXD).

UHDAS writes a sentence from its computer's clock beside each GPS sentence: `$PYRTM,2019,
275.8333405,75.8928819` (nav31: year, decimal day of year, days since boot) or `$UNIXD,
0.9166745,2.1294175` (uhdas: decimal day of year, days since boot). The decimal day counts from
0 at January 1 00:00 UTC, so day 275.83 of 2019 is October 3, 20:00, and runs past 365 when a
cruise crosses the new year. UNIXD has no year: it comes from the file name, UHDAS's
`<prefix><year>_<day>_<seconds>.gps` (`bh2019_275_72000.gps`, `name_year`).

Each GGA takes the date that puts it nearest the clock sentence before it (the one after it,
before the first), counting across midnight. The time of the fix is the GGA's: the UHDAS clock
can run minutes off the receiver (BH19-30: about 4 minutes ahead).

NavManager reads nav31 on its `pyrtm_parser` branch and UNIXD on `uhdas_parser`, both buffered
like its GGA + ZDA parsers (`docs/navmanager-differences.md`).
"""

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import PurePath

from nav_qa_processing.formats.gga_zda import (
    FAILED_CHECKSUM,
    NO_TIME,
    Fixes,
    Result,
    SpanCache,
    count_skipped,
    date_from,
    has_star,
    report_cut_short,
    sentences,
)
from nav_qa_processing.formats.nmea import GGA, parse_sentence
from nav_qa_processing.readable import plural

_DAY = 86_400 * 10**9  # [ns]
_NAME_YEAR = re.compile(r"((?:19|20)\d\d)_(\d{3})_\d+")
NO_CLOCK = "GGA with no clock sentence in its file to date it"
_spans = SpanCache()


def name_year(name: str) -> int | None:
    """The year in a UHDAS file name, `<prefix><year>_<day>_<seconds>`, or None"""
    match = _NAME_YEAR.search(PurePath(name).name)
    return int(match.group(1)) if match else None


def clock(fields: list[str], year: int | None) -> tuple[date, int] | None:
    """A PYRTM's or UNIXD's (date, time of day [ns]); UNIXD's year is given. None if unreadable"""
    try:
        if fields[0].endswith("PYRTM"):
            year, decimal_day = int(fields[1]), float(fields[2])
        else:
            decimal_day = float(fields[1])
    except (IndexError, ValueError):
        return None
    if year is None or not 0 <= decimal_day < 400:
        return None
    ns = round(decimal_day * _DAY)
    days, time_of_day = divmod(ns, _DAY)
    return date(year, 1, 1) + timedelta(days=days), time_of_day


def _dated(name: str, lines: list[str], kind: str) -> tuple[Fixes, int, int, int, int]:
    """A file's fixes, dated by the nearest clock sentence; with the GGAs without a time, those
    without a clock sentence in the file, and those that failed their checksum
    """
    year = name_year(name) if kind == "UNIXD" else None
    fixes = Fixes()
    pending: list[tuple[GGA, int]] = []  # GGAs before the file's first clock sentence
    last = None
    timeless = rejected = cut = 0
    for number, text in sentences(lines):
        sentence = parse_sentence(text)
        if sentence is None:
            if "GGA" in text:
                rejected += has_star(text)
                cut += not has_star(text)
            continue
        if sentence.fields[0].endswith(kind):  # $PYRTM or $UNIXD
            found = clock(sentence.fields, year)
            if found is not None:
                last = found
                for gga, number in pending:
                    fixes.append(gga, name, number, date_from(*last, gga.time.ns))
                pending = []
            continue
        if sentence.kind != "GGA":
            continue
        gga = GGA.from_fields(sentence.fields)
        if gga.time is None:
            timeless += 1
            continue
        if last is None:
            pending.append((gga, number))
        else:
            fixes.append(gga, name, number, date_from(*last, gga.time.ns))
    return fixes, timeless, len(pending), rejected, cut


def span(
    lines: list[str], name: str = "", kind: str = "PYRTM"
) -> tuple[datetime, datetime] | None:
    """The first and last fix of a file, to select and order files by"""
    dated = _dated(name, lines, kind)
    _spans.keep(name, lines, dated)
    fixes = dated[0]
    return (fixes[0].moment, fixes[-1].moment) if fixes else None


def read(files: Iterable[tuple[str, list[str]]], kind: str = "PYRTM") -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given, by `kind`'s clock
    sentences, PYRTM or UNIXD
    """
    result = Result(Fixes(), [], {}, {})
    for name, lines in files:
        fixes, timeless, unclocked, rejected, cut = _spans.take(name, lines) or _dated(
            name, lines, kind
        )
        result.fixes += fixes
        if timeless:
            result.errors.append(f"{name}: {plural(timeless, 'GGA')} without a time, skipped")
        if unclocked:
            result.errors.append(
                f"{name}: {plural(unclocked, 'GGA')} with no {kind} sentence to date it, skipped"
            )
        if rejected:
            result.rejected_checksums[name] = rejected
        count_skipped(result.skipped, name, NO_TIME, timeless)
        count_skipped(result.skipped, name, NO_CLOCK.replace("clock", kind), unclocked)
        count_skipped(result.skipped, name, FAILED_CHECKSUM, rejected)
        report_cut_short(result.errors, result.skipped, name, cut)
    _spans.clear()
    return result
