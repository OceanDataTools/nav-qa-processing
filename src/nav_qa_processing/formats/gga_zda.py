"""GGA fixes dated by ZDA sentences: the reader behind nav29 (and the other GGA + ZDA formats).

A GGA carries only the time of day. NavManager dates it from the ZDA sentences around it,
working on the GGAs buffered since the previous ZDA. Behavior reproduced from its output (and
documented here because it decides which records and dates R2R publishes):

- Any line prefix (a logger timestamp) is ignored. The text from the last `$` is the sentence.
- A GGA whose time of day equals that of the last GGA already dated is dropped. Only that one
  record is compared. Duplicates still in the buffer are kept, for QC to flag.
- At each ZDA, the leading GGAs with a time of day at or before the ZDA's get the ZDA's date.
  Each later GGA keeps its predecessor's date if its time is later, and is moved to the next day
  otherwise (an equal time also advances the day).
- If the first buffered GGA is later in the day than the ZDA, the whole buffer belongs around
  midnight before it: GGAs at or before the ZDA's time get its date, the rest the day before.
- "The last GGA dated" is nothing before the first one and after a ZDA with nothing buffered.
  NavManager then compares with time 0, so a GGA at exactly 00:00:00 is dropped.
- GGAs left after the last ZDA of the last file are dated from that ZDA (the next day if the
  first one is earlier than both the ZDA and the last GGA dated). The last of them is dropped:
  NavManager's final loop stops one record short.
- The buffer and the last ZDA carry across files.

Deviations, each recorded in `errors` when it happens:
- A GGA without a parseable time and a ZDA without a valid date are skipped. NavManager would
  date the GGA at midnight and write a malformed date for the ZDA.
- NavManager flushes a buffer of 86,400 GGAs that saw no ZDA, with undefined results. This
  reader keeps buffering and dates them at the next ZDA.
- With no ZDA at all NavManager exits. This reader drops the undated GGAs and records why.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise

from nav_qa_processing.formats.nmea import GGA, ZDA, parse_sentence

_DAY = 86_400 * 10**9  # [ns]
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
class _State:
    buffer: list[DatedFix] = field(default_factory=list)
    zda_day: date | None = None
    zda_ns: int | None = None
    last_day: date | None = None
    last_ns: int | None = None  # None before the first and after an empty flush


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


def _flush_at_zda(state: _State, out: list[DatedFix]) -> None:
    buffer, zda_day, zda_ns = state.buffer, state.zda_day, state.zda_ns
    if not buffer:
        state.last_day, state.last_ns = None, None
        return
    lead = 0
    while lead < len(buffer) and buffer[lead].gga.time.ns <= zda_ns:
        buffer[lead].day = zda_day
        lead += 1
    if lead:
        for prev, fix in zip(buffer[lead - 1 :], buffer[lead:], strict=False):
            later = fix.gga.time.ns > prev.gga.time.ns
            fix.day = prev.day if later else prev.day + timedelta(days=1)
    else:
        for fix in buffer:
            same = fix.gga.time.ns <= zda_ns
            fix.day = zda_day if same else zda_day - timedelta(days=1)
    _emit(state, out)


def _flush_trailing(state: _State, out: list[DatedFix]) -> None:
    buffer = state.buffer
    first = buffer[0]
    if first.gga.time.ns >= state.zda_ns:
        first.day = state.zda_day
    elif state.last_day is not None and first.gga.time.ns >= (state.last_ns or 0):
        first.day = state.last_day
    else:
        first.day = state.zda_day + timedelta(days=1)
    for prev, fix in pairwise(buffer):
        earlier = fix.gga.time.ns < prev.gga.time.ns
        fix.day = prev.day + timedelta(days=1) if earlier else prev.day
    state.buffer = buffer[:-1]  # NavManager's final loop stops one short
    _emit(state, out)


def _emit(state: _State, out: list[DatedFix]) -> None:
    out.extend(state.buffer)
    last = state.buffer[-1] if state.buffer else None
    state.last_day = last.day if last else None
    state.last_ns = last.gga.time.ns if last else None
    state.buffer = []


def read(files: list[tuple[str, list[str]]], navmanager_checksum: bool = True) -> Result:
    """Dated GGA fixes from `files`, each (name, lines), in the order given"""
    state = _State()
    out: list[DatedFix] = []
    errors: list[str] = []
    rejected: dict[str, int] = {}
    for name, lines in files:
        for number, line in enumerate(lines, start=1):
            if "$" not in line:
                continue
            sentence = parse_sentence(line, navmanager_checksum)
            if sentence is None:
                if "GGA" in line or "ZDA" in line:  # only the sentences this reader uses
                    rejected[name] = rejected.get(name, 0) + 1
                continue
            match sentence.kind:
                case "GGA":
                    gga = GGA.from_fields(sentence.fields)
                    if gga.time is None:
                        errors.append(f"{name}:{number}: GGA without a time, skipped")
                        continue
                    if gga.time.ns == (state.last_ns or 0):
                        continue
                    state.buffer.append(DatedFix(gga, name, number))
                case "ZDA":
                    zda = ZDA.from_fields(sentence.fields)
                    day = _zda_day(zda)
                    if day is None or zda.time is None:
                        errors.append(f"{name}:{number}: ZDA without a valid date, skipped")
                        continue
                    state.zda_day, state.zda_ns = day, zda.time.ns
                    _flush_at_zda(state, out)
    if state.buffer:
        if state.zda_day is None:
            errors.append(f"no ZDA in any file: {len(state.buffer)} GGA fixes can't be dated")
        else:
            _flush_trailing(state, out)
    return Result(out, errors, rejected)
