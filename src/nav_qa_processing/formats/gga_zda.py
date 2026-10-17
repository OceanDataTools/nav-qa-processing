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

import re
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from nav_qa_processing.formats.nmea import GGA, ZDA, has_checksum, parse_sentence
from nav_qa_processing.readable import plural

_DAY = 86_400 * 10**9  # [ns]
_HALF_DAY = _DAY // 2
_EPOCH = date(1970, 1, 1)
# A logger timestamp starting a line: Unix seconds or ISO 8601, then a space or a comma
_TIMESTAMP = re.compile(r"\s*(\d+(\.\d*)?|\d{4}-\d\d-\d\dT[\d:.]+Z?)[ ,]")
# A split sentence is joined with at most this many following lines
MAX_CONTINUATIONS = 3
# ZDA years taken as dates: the GPS era. Others are corrupt (e.g. a truncated year)
YEARS = range(1980, 2101)


@dataclass
class DatedFix:
    gga: GGA
    source_file: str
    line: int
    day: date | None = None

    @property
    def time_ns(self) -> int:
        """Nanoseconds since 1970-01-01T00:00:00Z"""
        return (self.day - _EPOCH).days * _DAY + self.gga.time.ns


# Why a reader skipped a sentence, as Result.skipped counts them (#67)
NO_TIME = "GGA without a time"
NO_VALID_DATE = "ZDA without a valid date"
NO_LOGGER_TIMESTAMP = "GGA without a logger timestamp"
NO_ZDA_TO_DATE_IT = "GGA with no ZDA in any file to date it"
FAILED_CHECKSUM = "failed its checksum"


@dataclass
class Result:
    fixes: list[DatedFix]
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


def _apart(a_ns: int, b_ns: int) -> int:
    """How far apart two times of day are [ns], across midnight"""
    difference = abs(a_ns - b_ns) % _DAY
    return min(difference, _DAY - difference)


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
        text = line[start:].rstrip("\r\n")
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
    """The first and last valid ZDA times in a file, to select and order files by"""
    times = []
    for line in lines:
        if "ZDA" not in line:
            continue
        sentence = parse_sentence(line)
        if sentence is None or sentence.kind != "ZDA":
            continue
        zda = ZDA.from_fields(sentence.fields)
        try:
            day = datetime(zda.year, zda.month, zda.day, tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if zda.time is not None:
            times.append(day + pd.Timedelta(zda.time.ns, "ns").to_pytimedelta())
    return (times[0], times[-1]) if times else None


def read(files: list[tuple[str, list[str]]]) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given"""
    fixes: list[DatedFix] = []
    fix_positions: list[int] = []
    zdas: list[tuple[date, int]] = []  # (date, time of day [ns])
    zda_positions: list[int] = []
    errors: list[str] = []
    rejected: dict[str, int] = {}
    skipped: dict[str, dict[str, int]] = {}
    position = 0  # sentence count across files
    for name, lines in files:
        timeless = undated = 0
        for number, line in sentences(lines):
            sentence = parse_sentence(line)
            if sentence is None:
                if "GGA" in line or "ZDA" in line:  # only the sentences this reader uses
                    rejected[name] = rejected.get(name, 0) + 1
                continue
            position += 1
            match sentence.kind:
                case "GGA":
                    gga = GGA.from_fields(sentence.fields)
                    if gga.time is None:
                        timeless += 1
                        continue
                    fixes.append(DatedFix(gga, name, number))
                    fix_positions.append(position)
                case "ZDA":
                    zda = ZDA.from_fields(sentence.fields)
                    day = _zda_day(zda)
                    if day is None or zda.time is None:
                        undated += 1
                        continue
                    zdas.append((day, zda.time.ns))
                    zda_positions.append(position)
        if timeless:
            errors.append(f"{name}: {plural(timeless, 'GGA')} without a time, skipped")
        if undated:
            errors.append(f"{name}: {plural(undated, 'ZDA')} without a valid date, skipped")
        count_skipped(skipped, name, NO_TIME, timeless)
        count_skipped(skipped, name, NO_VALID_DATE, undated)
        count_skipped(skipped, name, FAILED_CHECKSUM, rejected.get(name, 0))

    if not zdas:
        if fixes:
            errors.append(f"no ZDA in any file: {len(fixes)} GGA fixes can't be dated")
        for fix in fixes:
            count_skipped(skipped, fix.source_file, NO_ZDA_TO_DATE_IT, 1)
        return Result([], errors, rejected, skipped)
    for fix, at in zip(fixes, fix_positions, strict=True):
        following = bisect_left(zda_positions, at)
        candidates = [i for i in (following, following - 1) if 0 <= i < len(zdas)]
        # the one closer in time of day, across midnight; a tie goes to the following ZDA
        nearest = min(candidates, key=lambda i: _apart(zdas[i][1], fix.gga.time.ns))
        zda_day, zda_ns = zdas[nearest]
        fix.day = date_from(zda_day, zda_ns, fix.gga.time.ns)
    return Result(fixes, errors, rejected, skipped)
