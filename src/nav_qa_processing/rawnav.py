"""Reads a raw nav fileset into one table: RawNav.

Files are selected and ordered as NavManager's navdatalist does: a file takes part if its span
overlaps the [start, end] window, and files are read in order of their span's start. The span
is the format's (`formats.Reader.span`): first to last ZDA for GGA + ZDA formats, first to last
logger timestamp for nav33. Records are not clipped to the window (NavManager doesn't either).
Files without a span are listed in `errors` and skipped.
"""

import bz2
import gzip
import re
from collections import Counter
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
    selected: list[tuple[datetime, str, list[str]]] = []
    skipped: list[str] = []
    unread: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: _natural_key(p.name)):
        try:
            lines = read_lines(path)
        except OSError as error:
            errors.append(f"{path.name}: {error}")
            unread[path.name] = str(error)
            continue
        span = spec.reader.span(lines)
        if span is None:
            errors.append(f"{path.name}: no {spec.reader.dated_by}, skipped")
            unread[path.name] = f"no {spec.reader.dated_by}"
        elif span[1] < start or span[0] > end:
            skipped.append(path.name)
        else:
            selected.append((span[0], path.name, lines))
    selected.sort(key=lambda s: s[0])

    result = spec.reader.read([(name, lines) for _, name, lines in selected])
    errors += result.errors
    errors += [
        f"{name}: {count} {spec.reader.sentences} sentences failed their checksum"
        for name, count in result.rejected_checksums.items()
    ]

    fixes = result.fixes
    ggas = [f.gga for f in fixes]
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                np.array([f.time_ns for f in fixes], dtype=np.int64), unit="ns", utc=True
            ),
            "lon": pd.array([g.lon for g in ggas], dtype="Float64").astype(float),
            "lat": pd.array([g.lat for g in ggas], dtype="Float64").astype(float),
            "quality": pd.array([g.quality for g in ggas], dtype="Int64"),
            "nsat": pd.array([g.nsat for g in ggas], dtype="Int64"),
            "hdop": pd.array([g.hdop for g in ggas], dtype="Float64").astype(float),
            "alt": pd.array([g.alt for g in ggas], dtype="Float64").astype(float),
            "source_file": pd.Categorical([f.source_file for f in fixes]),
            "line": np.array([f.line for f in fixes], dtype=np.int64),
        }
    )
    decimals = {
        "time": max((g.time.decimals for g in ggas), default=0),
        "lon": max((g.lon_decimals for g in ggas if g.lon is not None), default=0),
        "lat": max((g.lat_decimals for g in ggas if g.lat is not None), default=0),
        "hdop": max((g.hdop_decimals for g in ggas if g.hdop is not None), default=0),
        "alt": max((g.alt_decimals for g in ggas if g.alt is not None), default=0),
    }
    talkers = dict(sorted(Counter(g.talker for g in ggas if g.talker).items()))
    return RawNav(
        frame,
        decimals,
        [name for _, name, _ in selected],
        skipped,
        errors,
        talkers=talkers,
        skipped_lines=result.skipped,
        unread_files=unread,
    )
