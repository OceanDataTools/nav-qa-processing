"""Raw navigation formats: one FormatSpec per R2R format ID (NavManager's nav1 … nav33).

Every format ID has a definition in formats/definitions/ (see catalog.py). The ones with a
reader can be processed; the reader turns a fileset's lines into dated fixes. Formats that
share a reader (most are "some line prefix plus NMEA GGA + ZDA") differ only in their spec.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from nav_qa_processing.formats import catalog, gga_zda, timestamped_gga


@dataclass(frozen=True)
class Reader:
    """How a format's files are read: `read` dates their fixes; `span` is a file's first and
    last time, by which files are selected and ordered (None: the file can't be dated)
    """

    read: Callable[[list[tuple[str, list[str]]]], gga_zda.Result]
    span: Callable[[list[str]], tuple[datetime, datetime] | None]
    dated_by: str  # what dates a file, for "no …, skipped"
    sentences: str  # the sentences read, for checksum failures


GGA_ZDA = Reader(gga_zda.read, gga_zda.span, "ZDA sentence", "GGA/ZDA")
TIMESTAMPED_GGA = Reader(timestamped_gga.read, timestamped_gga.span, "logger timestamp", "GGA")

# Format ID -> reader, for the formats implemented so far
READERS: dict[str, Reader] = {
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


def all_formats() -> dict[str, FormatSpec]:
    return {
        format_id: FormatSpec(definition, READERS.get(format_id))
        for format_id, definition in catalog.formats().items()
    }


def get(format_id: str) -> FormatSpec:
    """The spec of a format that can be read"""
    spec = all_formats().get(format_id)
    if spec is None or not spec.supported:
        supported = ", ".join(sorted(READERS))
        what = "not supported yet" if spec else "unknown"
        raise ValueError(f"nav format {format_id!r} is {what} (supported: {supported})")
    return spec
