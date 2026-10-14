"""GGA fixes dated by ZDA sentences: the reader behind nav29 (and the other GGA + ZDA formats).

A GGA carries only the time of day. Each GGA takes its date from the ZDA nearest to it in the
stream of sentences (across files, in the order given; a tie goes to the following ZDA). If the
two times of day are more than 12 hours apart, they straddle midnight, and the date moves a day:
back when the GGA's time is the later one, forward when it's the earlier. This assumes the
nearest ZDA is within 12 hours of the GGA, which holds for any receiver that sends ZDA.

- Any line prefix (a logger timestamp) is ignored. The text from the last `$` is the sentence.
- Every GGA with a parseable time is kept, including repeated or out-of-order times: QC decides
  what is a duplicate or out of sequence.

NavManager dates GGAs buffer by buffer, which drops records and misdates some. None of it is
reproduced (`CLAUDE.md`, "What parity with R2R means"):
- it drops the last GGA after the final ZDA of the last file (its final loop stops one short):
  R2R's SP2424 bestres lacks its final fix, 2024-11-04T03:16:44.50Z (#14);
- it drops a GGA whose time of day equals that of the last GGA it dated, and a GGA at exactly
  00:00:00 before anything is dated (#14);
- after the GGAs a ZDA dates, an equal time of day advances the date by a day (#15).

Recorded in `errors`, not raised:
- a GGA without a parseable time and a ZDA without a valid date are skipped;
- with no valid ZDA at all, no GGA can be dated, and none is returned.
"""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta

from nav_qa_processing.formats.nmea import GGA, ZDA, parse_sentence

_DAY = 86_400 * 10**9  # [ns]
_HALF_DAY = _DAY // 2
_EPOCH = date(1970, 1, 1)


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


@dataclass
class Result:
    fixes: list[DatedFix]
    errors: list[str]  # "<file>: <message>"
    rejected_checksums: dict[str, int]  # GGA and ZDA sentences, per file


def _zda_day(zda: ZDA) -> date | None:
    try:
        return date(zda.year, zda.month, zda.day)
    except (TypeError, ValueError):
        return None


def date_from(zda_day: date, zda_ns: int, gga_ns: int) -> date:
    """The date of a GGA at time of day `gga_ns`, from a nearby ZDA's date and time of day"""
    difference = gga_ns - zda_ns
    if difference > _HALF_DAY:  # GGA before midnight, ZDA after it
        return zda_day - timedelta(days=1)
    if difference < -_HALF_DAY:  # GGA after midnight, ZDA before it
        return zda_day + timedelta(days=1)
    return zda_day


def read(files: list[tuple[str, list[str]]]) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given"""
    fixes: list[DatedFix] = []
    fix_positions: list[int] = []
    zdas: list[tuple[date, int]] = []  # (date, time of day [ns])
    zda_positions: list[int] = []
    errors: list[str] = []
    rejected: dict[str, int] = {}
    position = 0  # sentence count across files
    for name, lines in files:
        for number, line in enumerate(lines, start=1):
            if "$" not in line:
                continue
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
                        errors.append(f"{name}:{number}: GGA without a time, skipped")
                        continue
                    fixes.append(DatedFix(gga, name, number))
                    fix_positions.append(position)
                case "ZDA":
                    zda = ZDA.from_fields(sentence.fields)
                    day = _zda_day(zda)
                    if day is None or zda.time is None:
                        errors.append(f"{name}:{number}: ZDA without a valid date, skipped")
                        continue
                    zdas.append((day, zda.time.ns))
                    zda_positions.append(position)

    if not zdas:
        if fixes:
            errors.append(f"no ZDA in any file: {len(fixes)} GGA fixes can't be dated")
        return Result([], errors, rejected)
    for fix, at in zip(fixes, fix_positions, strict=True):
        following = bisect_left(zda_positions, at)
        if following == len(zda_positions):
            nearest = following - 1
        elif following == 0:
            nearest = 0
        else:  # the nearer of the ZDAs before and after; a tie goes to the following one
            before, after = zda_positions[following - 1], zda_positions[following]
            nearest = following if after - at <= at - before else following - 1
        zda_day, zda_ns = zdas[nearest]
        fix.day = date_from(zda_day, zda_ns, fix.gga.time.ns)
    return Result(fixes, errors, rejected)
