"""Raw navigation formats: one FormatSpec per R2R format ID (NavManager's nav1 … nav33, written
nav01 … nav33 here; `catalog.canonical_id` reads the one-digit spelling too).

Every format ID has a definition in formats/definitions/ (see catalog.py). The ones with a
reader can be processed; the reader turns a fileset's lines into dated fixes. Formats that
share a reader (most are "some line prefix plus NMEA GGA + ZDA") differ only in their spec.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from nav_qa_processing.formats import catalog, file_stamp, gga_zda, timestamped_gga, zda_or_stamp


@dataclass(frozen=True)
class Reader:
    """How a format's files are read: `read` dates their fixes; `span` is a file's first and
    last time, by which files are selected and ordered (None: the file can't be dated). A
    `named` reader's span also takes the file's name (nav22's date is in it)
    """

    read: Callable[[list[tuple[str, list[str]]]], gga_zda.Result]
    span: Callable[..., tuple[datetime, datetime] | None]
    dated_by: str  # what dates a file, for "no …, skipped"
    sentences: str  # the sentences read, for checksum failures
    named: bool = False

    def span_of(self, name: str, lines: list[str]) -> tuple[datetime, datetime] | None:
        """A file's span, by `span` with its name when the reader needs it"""
        return self.span(lines, name=name) if self.named else self.span(lines)


GGA_ZDA = Reader(gga_zda.read, gga_zda.span, "ZDA sentence", "GGA/ZDA")
TIMESTAMPED_GGA = Reader(timestamped_gga.read, timestamped_gga.span, "logger timestamp", "GGA")
SCS_GGA = Reader(
    partial(timestamped_gga.read, stamp=timestamped_gga.scs_time),
    partial(timestamped_gga.span, stamp=timestamped_gga.scs_time),
    "logger timestamp",
    "GGA",
)
ZDA_OR_STAMP = Reader(zda_or_stamp.read, zda_or_stamp.span, "ZDA or logger timestamp", "GGA/ZDA")
OSU_GGA = Reader(
    partial(timestamped_gga.read, stamp=timestamped_gga.osu_time),
    partial(timestamped_gga.span, stamp=timestamped_gga.osu_time),
    "logger timestamp",
    "GGA",
)
FILE_STAMP_GGA = Reader(
    file_stamp.read, file_stamp.span, "date in the file name", "GGA", named=True
)

# Format ID -> reader, for the formats implemented so far
READERS: dict[str, Reader] = {
    "nav01": GGA_ZDA,  # bare NMEA (#81)
    "nav02": SCS_GGA,  # NOAA SCS: logger date and time, then GGA (#81)
    "nav12": ZDA_OR_STAMP,  # device ID, year:day-of-year timestamp, NMEA; ZDA when logged
    "nav15": OSU_GGA,  # OSU DAS: DATA, ISO timestamp, quoted GGA
    "nav20": GGA_ZDA,  # NAV tag, logger date and time, device tag, then NMEA (Knorr)
    "nav22": FILE_STAMP_GGA,  # bare NMEA with no date: the file name's YYYYMMDDhhmmss
    "nav24": GGA_ZDA,  # Sikuliaq: logger ID and ISO timestamp, then NMEA (#81)
    "nav25": GGA_ZDA,  # Healy's Seapath: logger ID, ISO timestamp, NMEA, comma-separated
    "nav29": GGA_ZDA,
    "nav33": TIMESTAMPED_GGA,
}


@dataclass(frozen=True)
class FormatSpec:
    definition: catalog.FormatDefinition
    reader: Reader | None

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def supported(self) -> bool:
        return self.reader is not None


def readable() -> list[str]:
    """The IDs of the formats with a reader, in order (nav01, nav02, …, then named ones)"""
    return sorted(READERS, key=catalog.natural_key)


def all_formats() -> dict[str, FormatSpec]:
    return {
        format_id: FormatSpec(definition, READERS.get(format_id))
        for format_id, definition in catalog.formats().items()
    }


def get(format_id: str) -> FormatSpec:
    """The spec of a format that can be read (NavManager's one-digit IDs, e.g. nav2, too)"""
    format_id = catalog.canonical_id(format_id)
    spec = all_formats().get(format_id)
    if spec is None or not spec.supported:
        supported = ", ".join(readable())
        what = "not supported yet" if spec else "unknown"
        raise ValueError(f"nav format {format_id!r} is {what} (supported: {supported})")
    return spec
