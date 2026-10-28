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
at 2 Hz ran a 4 GB OpenVDM server out of memory).
"""

import bz2
import gzip
import re
from collections.abc import Iterator
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


def read_lines(path: Path) -> list[str]:
    opener = {".gz": gzip.open, ".bz2": bz2.open}.get(path.suffix, open)
    with opener(path, "rt", encoding="ascii", errors="replace", newline=None) as f:
        return f.read().splitlines()


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
    selected.sort(key=lambda s: s[0])

    result = spec.reader.read(_read_again(selected, errors, unread))
    errors += result.errors
    errors += [
        f"{name}: {count} {spec.reader.sentences} sentences failed their checksum"
        for name, count in result.rejected_checksums.items()
    ]

    # Each column is taken from the reader's as the frame is built, so they're held once
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
            "time": pd.to_datetime(columns.pop("time"), unit="ns", utc=True),
            "lon": columns.pop("lon"),
            "lat": columns.pop("lat"),
            "quality": pd.array(columns.pop("quality"), dtype="Float64").astype("Int64"),
            "nsat": pd.array(columns.pop("nsat"), dtype="Float64").astype("Int64"),
            "hdop": columns.pop("hdop"),
            "alt": columns.pop("alt"),
            "source_file": files.reorder_categories(sorted(names)),
            "line": columns.pop("line").astype(np.int64),
        }
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


def _most(decimals: np.ndarray) -> int:
    return int(decimals.max()) if len(decimals) else 0
