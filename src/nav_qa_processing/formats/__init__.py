"""Raw navigation formats: one FormatSpec per R2R format ID (NavManager's nav1 … nav33).

Every format ID has a definition in formats/definitions/ (see catalog.py). The ones with a
reader can be processed; the reader turns a fileset's lines into dated fixes. Formats that
share a reader (most are "some line prefix plus NMEA GGA + ZDA") differ only in their spec.
"""

from collections.abc import Callable
from dataclasses import dataclass

from nav_qa_processing.formats import catalog, gga_zda

# Format ID -> reader, for the formats implemented so far
READERS: dict[str, Callable[..., gga_zda.Result]] = {
    "nav29": gga_zda.read,
}


@dataclass(frozen=True)
class FormatSpec:
    definition: catalog.FormatDefinition
    reader: Callable[..., gga_zda.Result] | None

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
