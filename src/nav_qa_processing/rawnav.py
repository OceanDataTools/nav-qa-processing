"""Reads a raw nav fileset into one table: RawNav.

Files are selected and ordered as NavManager's navdatalist does: a file takes part if its span
overlaps the [start, end] window, and files are read in order of their span's start. The span
is the format's (`formats.Reader.span`): first to last ZDA for GGA + ZDA formats, first to last
logger timestamp for nav33. Records are not clipped to the window (NavManager doesn't either).
Files without a span are listed in `errors` and skipped.

Memory (#157): a file's lines are read to find its span and dropped, and the selected files are
read again, one at a time, as the reader goes; the reader keeps its fixes as columns
(`gga_zda.Fixes`). A run's memory is then about the largest file's lines plus about 90 bytes
per fix, not every file's lines plus about 660 bytes per fix as Python objects (a 20-day cruise
at 2 Hz ran a 4 GB OpenVDM server out of memory). A file's lines are `Lines` (#175): its bytes
and where each line starts and ends, about 1 byte a character and 16 a line, rather than its
text and a list of strings (SR2612's 396 MB file: 1.1 GB as both).
"""

import bz2
import gzip
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nav_qa_processing import formats
from nav_qa_processing import quantities as q

GPS_START = datetime(1994, 3, 1, tzinfo=UTC)
GPS_END = datetime(2034, 12, 31, tzinfo=UTC)

# RawNav's record columns, in r2rnav order, and the quantity each one is
QUANTITIES = {
    "time": q.ISO_TIME,
    "lon": q.SHIP_LONGITUDE,
    "lat": q.SHIP_LATITUDE,
    "quality": q.NMEA_QUALITY,
    "nsat": q.NSV,
    "hdop": q.HDOP,
    "alt": q.ANTENNA_HEIGHT,
}


@dataclass
class RawNav:
    """Pre-QC fixes, one row per record in file order, plus what's needed to write them back.

    `frame` columns: time (datetime64[ns, UTC]), lon, lat, quality, nsat, hdop, alt, source_file,
    line. `decimals` holds the receiver's precision per column ("time" is the fractional-second
    digits).
    """

    frame: pd.DataFrame
    decimals: dict[str, int]
    files: list[str]  # read, in order
    skipped_files: list[str] = field(default_factory=list)  # outside the window
    errors: list[str] = field(default_factory=list)
    # GGA talker IDs and how many fixes each gave, e.g. {"GP": 56925}: whether the feed is a
    # GPS or GNSS receiver (GP, GN) or an INS (IN), as the data says
    talkers: dict[str, int] = field(default_factory=dict)
    # What the reader couldn't use (#67): sentences per file and reason (gga_zda's reasons),
    # and files it couldn't read at all, with why
    skipped_lines: dict[str, dict[str, int]] = field(default_factory=dict)
    unread_files: dict[str, str] = field(default_factory=dict)
    # Each skipped file's span (first and last time), to say where it lies (#122)
    skipped_spans: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)


def read_lines(path: Path) -> "Lines":
    opener = {".gz": gzip.open, ".bz2": bz2.open}.get(path.suffix, open)
    with opener(path, "rb") as f:
        return Lines(f.read())


# What str.splitlines splits an ASCII text on, once universal newlines have made \r and \r\n
# into \n: the bytes ending a line, \r\n one ending
LINE_ENDS = b"\n\r\x0b\x0c\x1c\x1d\x1e"
_ENDS = np.zeros(256, dtype=bool)
_ENDS[list(LINE_ENDS)] = True
_CHUNK = 1 << 24  # [bytes] searched for line ends at a time
_LINES_AT_A_TIME = 1 << 16  # lines' bounds made Python ints at a time, as they're iterated
_INT32_BYTES = 2**31 - 1  # [bytes] a file's bounds are int32 below this


class Lines(Sequence[str]):
    """A file's lines as `str.splitlines` gave them from its text read as ASCII (a byte that
    isn't one read as U+FFFD) with universal newlines, held as its bytes and where each line
    starts and ends rather than as a list of strings (#175). `release` lets the bytes go once
    a reader is done with them; reading them after is an error
    """

    def __init__(self, data: bytes):
        self._data: bytes | None = data
        self._starts, self._ends = _line_bounds(data)

    def release(self) -> None:
        self._data = None

    def _bytes(self) -> bytes:
        if self._data is None:
            raise ValueError("the file's lines were released: the reader had moved on")
        return self._data

    def __len__(self) -> int:
        return len(self._ends)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if not -len(self) <= index < len(self):
            raise IndexError(index)
        start, end = int(self._starts[index]), int(self._ends[index])
        return self._bytes()[start:end].decode("ascii", "replace")

    def __iter__(self) -> Iterator[str]:
        # A block of lines decoded at a time: decoding ASCII with "replace" gives a character
        # a byte, so the bounds index the block's text as they do its bytes
        for first in range(0, len(self), _LINES_AT_A_TIME):
            part = slice(first, first + _LINES_AT_A_TIME)
            starts, ends = self._starts[part].tolist(), self._ends[part].tolist()
            base = starts[0]
            text = self._bytes()[base : ends[-1]].decode("ascii", "replace")
            for start, end in zip(starts, ends):
                yield text[start - base : end - base]


def _line_bounds(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Where each line starts and ends (the index of the byte after it) in `data`, as int32
    for a file under 2 GiB
    """
    size = len(data)
    dtype = np.int32 if size < _INT32_BYTES else np.int64
    if not size:
        return np.zeros(0, dtype), np.zeros(0, dtype)
    view = np.frombuffer(data, np.uint8)
    ends = np.concatenate([_ends_in(view, start, dtype) for start in range(0, size, _CHUNK)])
    if not len(ends):  # one line, without an end
        return np.zeros(1, dtype), np.full(1, size, dtype)
    # \r\n is one end, 2 bytes wide: its \r ends the line
    following = np.add(ends, 1, dtype=np.int64)
    np.minimum(following, size - 1, out=following)
    crlf = (view[ends] == 13) & (view[following] == 10)
    del following
    crlf[-1] &= int(ends[-1]) + 1 < size
    starts = np.empty(len(ends), dtype)
    starts[0] = 0
    np.add(ends[:-1], 1, out=starts[1:])
    starts[1:] += crlf[:-1]
    last = int(ends[-1]) + 1 + int(crlf[-1])
    if last < size:  # a last line without an end
        starts, ends = np.append(starts, last), np.append(ends, size)
    return starts, ends


def _ends_in(view: np.ndarray, offset: int, dtype) -> np.ndarray:
    """The line ends' positions in the chunk of `view` from `offset`: the bytes below 31
    (control characters, few but the line ends), then those that end a line, but a \n after
    a \r, whose \r ended the line (so a file with \r\n has an end a line, not two)
    """
    chunk = view[offset : offset + _CHUNK]
    candidates = np.flatnonzero(chunk < 31)
    candidates = candidates[_ENDS[chunk[candidates]]]
    after_cr = (chunk[candidates] == 10) & (view[np.maximum(candidates + offset - 1, 0)] == 13)
    after_cr &= candidates + offset > 0
    return (candidates[~after_cr] + offset).astype(dtype)


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def read_fileset(
    paths: list[Path],
    format_id: str,
    start: datetime = GPS_START,
    end: datetime = GPS_END,
) -> RawNav:
    """RawNav from the raw files `paths` in format `format_id`"""
    spec = formats.get(format_id)
    errors: list[str] = []
    selected: list[tuple[datetime, str, Path]] = []
    skipped: dict[str, tuple[datetime, datetime]] = {}
    unread: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: _natural_key(p.name)):
        try:
            lines = read_lines(path)
        except OSError as error:
            errors.append(f"{path.name}: {error}")
            unread[path.name] = str(error)
            continue
        span = spec.reader.span_of(path.name, lines)
        if span is None:
            errors.append(f"{path.name}: no {spec.reader.dated_by}, skipped")
            unread[path.name] = f"no {spec.reader.dated_by}"
        elif span[1] < start or span[0] > end:
            skipped[path.name] = span
        else:
            selected.append((span[0], path.name, path))
        del lines  # not the last file's held through the read
    selected.sort(key=lambda s: s[0])

    result = spec.reader.read(_read_again(selected, errors, unread))
    errors += result.errors
    errors += [
        f"{name}: {count} {spec.reader.sentences} sentences failed their checksum"
        for name, count in result.rejected_checksums.items()
    ]

    # Each column is taken from the reader's as the frame is built, and the frame keeps it
    # rather than a copy (copy=False: each column its own block, not one block of the floats;
    # the time viewed as datetime64 and localized), so they're held once (#174)
    names, talker_ids = result.fixes.files, result.fixes.talkers
    columns = result.fixes.drain()
    decimals = {"time": _most(columns.pop("time_decimals"))}
    for name in ("lon", "lat", "hdop", "alt"):
        decimals[name] = _most(columns.pop(f"{name}_decimals")[~np.isnan(columns[name])])
    counts = np.bincount(columns.pop("talker"), minlength=len(talker_ids))
    talkers = {t: int(n) for t, n in sorted(zip(talker_ids, counts, strict=True)) if t and n}
    files = pd.Categorical.from_codes(columns.pop("file"), categories=names)
    frame = pd.DataFrame(
        {
            "time": pd.Series(columns.pop("time").view("M8[ns]")).dt.tz_localize("UTC"),
            "lon": columns.pop("lon"),
            "lat": columns.pop("lat"),
            "quality": pd.array(columns.pop("quality"), dtype="Float64").astype("Int64"),
            "nsat": pd.array(columns.pop("nsat"), dtype="Float64").astype("Int64"),
            "hdop": columns.pop("hdop"),
            "alt": columns.pop("alt"),
            "source_file": files.reorder_categories(sorted(names)),
            "line": columns.pop("line").astype(np.int64),
        },
        copy=False,
    )
    return RawNav(
        frame,
        decimals,
        [name for _, name, _ in selected if name not in unread],
        list(skipped),
        errors,
        talkers=talkers,
        skipped_lines=result.skipped,
        unread_files=unread,
        skipped_spans=skipped,
    )


def _read_again(
    selected: list[tuple[datetime, str, Path]], errors: list[str], unread: dict[str, str]
) -> Iterator[tuple[str, list[str]]]:
    """Each selected file's (name, lines), read as the reader comes to it, so only one file's
    lines are held at a time; one that can't be read now is recorded and passed over
    """
    for _, name, path in selected:
        try:
            lines = read_lines(path)
        except OSError as error:
            errors.append(f"{name}: {error}")
            unread[name] = str(error)
            continue
        yield name, lines
        lines.release()  # the reader asked for the next file, so it's done with this one


def _most(decimals: np.ndarray) -> int:
    return int(decimals.max()) if len(decimals) else 0
