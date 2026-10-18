"""Fixes timed by the logger: the reader behind nav03 (NOAA SCS, mostly GLL).

nav03 is nav02's layout, `MM/DD/YYYY,hh:mm:ss.sss,` then a sentence, but the sentences are
mostly GLL, often cut short after the longitude (`$GPGLL,4646.622,N,09204.105,W`, no time,
status or checksum), with an occasional GGA. A GLL without its time can only take the logger's,
so every fix, GGA included, takes its line's logger date and time: one clock for the stream.
A GLL needs its position (5 fields) to be read; without a status its quality is missing.

NavManager's `parse_nav3()` (`include/parsers/nav3.php`) does the same: it times each GGA and
GLL by the logger and writes a GLL's quality from its status (missing: `NAN`).
"""

import re

from nav_qa_processing.formats.gga_zda import (
    FAILED_CHECKSUM,
    NO_LOGGER_TIMESTAMP,
    DatedFix,
    Result,
    count_skipped,
    has_star,
    report_cut_short,
    sentences,
)
from nav_qa_processing.formats.nmea import GGA, GLL, TimeOfDay, parse_sentence
from nav_qa_processing.formats.timestamped_gga import Stamp, scs_time
from nav_qa_processing.readable import plural

# A GLL cut after its longitude still has a position; a GGA needs its usual fields
MINIMUM = {"GLL": 5, "GGA": 13}
NO_POSITION = "sentence without a position"
_DECIMALS = re.compile(r"\d\d:\d\d:\d\d(?:\.(\d*))?")


def _logger_time(line: str, ns: int) -> TimeOfDay:
    match = _DECIMALS.search(line)
    return TimeOfDay(ns, len(match.group(1) or "") if match else 0)


def read(files: list[tuple[str, list[str]]], stamp: Stamp = scs_time) -> Result:
    """Fixes from the GLL and GGA sentences of `files`, each (name, lines), in the order given,
    timed by their lines' logger timestamps
    """
    result = Result([], [], {}, {})
    for name, lines in files:
        unstamped = placeless = rejected = cut = 0
        for number, text in sentences(lines):
            sentence = parse_sentence(text, MINIMUM)
            if sentence is None:
                if "GLL" in text or "GGA" in text:
                    rejected += has_star(text)
                    cut += not has_star(text)
                continue
            if sentence.kind == "GLL":
                gga = GLL.from_fields(sentence.fields).as_gga()
            elif sentence.kind == "GGA":
                gga = GGA.from_fields(sentence.fields)
            else:
                continue
            if gga.lat is None or gga.lon is None:
                placeless += 1
                continue
            logged = stamp(lines[number - 1])
            if logged is None:
                unstamped += 1
                continue
            day, ns = logged
            fix = DatedFix(
                GGA(**{**gga.__dict__, "time": _logger_time(lines[number - 1], ns)}), name, number
            )
            fix.day = day
            result.fixes.append(fix)
        if unstamped:
            result.errors.append(
                f"{name}: {plural(unstamped, 'fix', 'fixes')} without a logger timestamp, skipped"
            )
        if placeless:
            result.errors.append(
                f"{name}: {plural(placeless, 'GLL/GGA')} without a position, skipped"
            )
        if rejected:
            result.rejected_checksums[name] = rejected
        count_skipped(result.skipped, name, NO_LOGGER_TIMESTAMP.replace("GGA", "fix"), unstamped)
        count_skipped(result.skipped, name, NO_POSITION, placeless)
        count_skipped(result.skipped, name, FAILED_CHECKSUM, rejected)
        report_cut_short(result.errors, result.skipped, name, cut)
    return result
