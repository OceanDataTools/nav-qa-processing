"""GeoCSV 2.0 navigation products (R2R format 100157): `<CRUISE>_bestres|1min|control.geoCSV`.

The structure is R2R's (see "What R2R actually publishes today" in docs/architecture.md): the same
16 `#keyword: value` lines in the same order, then a header line of `field_standard_name`s, then
comma-separated records. Everything about a column (unit, datatype, long name, number format)
comes from `quantities.py`, so units and descriptions are this project's standard rather than
R2R's text (`meter\\second`, `degree_east`, swapped long names); values use the receiver's
precision where the quantity says so.

Flagged bestres records are written as R2R writes them (SP1925, product fileset 609448): `#`
followed by the record's measured fields only, time through antenna height. Speed and course,
which come from good fixes only, are left out rather than written `NAN`. GeoCSV readers skip the
line as a comment.

Header values that identify the source (`attribution`, `source_repository`, the cruise and
fileset DOIs) are passed in (`Source`): R2R's for R2R's processing, the operator's at sea, empty
when unknown.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nav_qa_processing import quantities as q
from nav_qa_processing.filenames import report_stem

STANDARD_NAME_CV = "http://www.rvdata.us/voc/fieldname"
ELLIPSOID = "WGS-84 (EPSG:4326)"
DELIMITER = ","


@dataclass(frozen=True)
class Product:
    name: str  # in the file name
    title: str
    columns: tuple[tuple[str, q.Quantity], ...]  # (DataFrame column, quantity)
    flagged_columns: int = 0  # fields written for a flagged record (0: all)


_TIME, _LON, _LAT = ("time", q.ISO_TIME), ("lon", q.SHIP_LONGITUDE), ("lat", q.SHIP_LATITUDE)
_SOG, _COG = ("sog", q.SPEED_MADE_GOOD), ("cog", q.COURSE_MADE_GOOD)
BESTRES = Product(
    "bestres",
    "Processed Trackline Navigation Data: Best Resolution",
    (_TIME, _LON, _LAT, ("quality", q.NMEA_QUALITY), ("nsat", q.NSV), ("hdop", q.HDOP),
     ("alt", q.ANTENNA_HEIGHT), _SOG, _COG),
    flagged_columns=7,  # time through antenna height: no SOG/COG for a flagged fix
)  # fmt: skip
ONE_MINUTE = Product(
    "1min", "Processed Trackline Navigation Data: One Minute Resolution",
    (_TIME, _LON, _LAT, _SOG, _COG),
)  # fmt: skip
CONTROL = Product(
    "control", "Processed Trackline Navigation Data: Control Points", (_TIME, _LON, _LAT)
)
PRODUCTS = {p.name: p for p in (BESTRES, ONE_MINUTE, CONTROL)}


@dataclass(frozen=True)
class Source:
    """Who and what the products come from; empty strings are written as empty values"""

    cruise_id: str
    cruise_doi: str = ""  # source_event, e.g. "doi:10.7284/911454"
    fileset_doi: str = ""  # source_dataset: the raw fileset
    attribution: str = ""
    repository: str = ""  # source_repository, e.g. R2R's "doi:10.17616/R39C8D"


def file_name(cruise_id: str, product: Product) -> str:
    """e.g. SP2613_bestres.geoCSV, valid on every platform whatever the ID"""
    return f"{report_stem(cruise_id)}_{product.name}.geoCSV"


def header(product: Product, source: Source, created: datetime) -> list[str]:
    quantities = [quantity for _, quantity in product.columns]
    keywords = [
        ("dataset", "GeoCSV 2.0"),
        ("title", product.title),
        ("field_unit", DELIMITER.join(x.geocsv_unit for x in quantities)),
        ("field_type", DELIMITER.join(x.datatype.value for x in quantities)),
        ("field_standard_name", DELIMITER.join(x.name for x in quantities)),
        ("field_long_name", DELIMITER.join(x.description for x in quantities)),
        ("standard_name_cv", STANDARD_NAME_CV),
        ("ellipsoid", ELLIPSOID),
        ("delimiter", DELIMITER),
        ("field_missing", q.MISSING),
        ("attribution", source.attribution),
        ("source_repository", source.repository),
        ("source_event", source.cruise_doi),
        ("source_dataset", source.fileset_doi),
        ("cruise_id", source.cruise_id),
        ("creation_date", q.format_datetime(created, 0)),
    ]
    return [f"#{key}: {value}" for key, value in keywords]


def write(
    directory: Path,
    product: Product,
    frame: pd.DataFrame,
    decimals: dict[str, int],
    source: Source,
    created: datetime | None = None,
    stem: str | None = None,
) -> Path:
    """Writes `frame` as `product` into `directory`, named after `stem` (default: the cruise
    ID); returns the file's path.

    `decimals` gives the receiver's precision for the columns whose quantity takes it (RawNav's
    `decimals`). A `flags` column, if present, marks flagged records (non-zero).
    """
    created = created or datetime.now(UTC)
    flagged = frame["flags"].to_numpy() != 0 if "flags" in frame else None
    lines = header(product, source, created)
    lines.append(DELIMITER.join(quantity.name for _, quantity in product.columns))
    path = Path(directory) / file_name(stem or source.cruise_id, product)
    with path.open("w") as file:
        file.writelines(line + "\n" for line in lines)
        file.writelines(
            line + "\n"
            for line in rows(
                frame, product.columns, decimals, flagged, product.flagged_columns, DELIMITER
            )
        )
    return path


# Rows formatted at a time: a fileset's products are written a chunk at a time, not held as text
# (a 3.5 M-fix bestres as strings took 2 GB, #157)
CHUNK = 100_000


def rows(
    frame: pd.DataFrame,
    columns: list[tuple[str, q.Quantity]],
    decimals: dict[str, int],
    flagged: np.ndarray | None,
    flagged_columns: int,
    delimiter: str,
) -> Iterator[str]:
    """Each record of `frame[columns]` as a line of text, formatted through its quantity, with
    `#` before a `flagged` record, which has only its first `flagged_columns` fields when that's
    set
    """
    short = flagged_columns or len(columns)
    for start in range(0, len(frame), CHUNK):
        chunk = frame.iloc[start : start + CHUNK]
        text = [
            [quantity.format(value, decimals.get(column)) for value in chunk[column]]
            for column, quantity in columns
        ]
        marks = flagged[start : start + CHUNK] if flagged is not None else [False] * len(chunk)
        for mark, values in zip(marks, zip(*text, strict=True), strict=True):
            yield "#" + delimiter.join(values[:short]) if mark else delimiter.join(values)


@dataclass
class GeoCSV:
    keywords: dict[str, str]  # in file order
    columns: list[str]  # field_standard_names
    records: pd.DataFrame  # by field_standard_name, typed by field_type, plus "flagged"


def read(path: Path) -> GeoCSV:
    """A GeoCSV nav product (ours or R2R's): keywords, columns and typed records. Lines starting
    with `#` and a digit are flagged records, which may have fewer fields (missing ones are
    NaN); other `#` lines are keywords or comments.
    """
    keywords: dict[str, str] = {}
    columns: list[str] = []
    rows: list[list[str]] = []
    flagged: list[bool] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        if line.startswith("#") and not line[1:2].isdigit():
            key, colon, value = line[1:].partition(":")
            if colon:
                keywords[key.strip()] = value.strip()
            continue
        if not columns:
            columns = [c.strip() for c in line.split(keywords.get("delimiter", DELIMITER))]
            continue
        flagged.append(line.startswith("#"))
        values = line.lstrip("#").split(keywords.get("delimiter", DELIMITER))
        rows.append(values + [None] * (len(columns) - len(values)))
    records = pd.DataFrame(rows, columns=columns)
    types = keywords.get("field_type", "").split(DELIMITER)
    missing = keywords.get("field_missing", q.MISSING)
    for name, kind in zip(columns, types, strict=False):
        values = records[name].replace(missing, np.nan).replace({None: np.nan})
        if kind == "datetime":
            records[name] = pd.to_datetime(values, utc=True, format="ISO8601")
        elif kind == "integer":
            records[name] = pd.to_numeric(values).astype("Int64")
        elif kind == "float":
            records[name] = pd.to_numeric(values)
    records["flagged"] = flagged
    return GeoCSV(keywords, columns, records)
