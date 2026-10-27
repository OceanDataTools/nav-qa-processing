"""Legacy r2rnav files (R2R format 100002): tab-separated, 3 `//` header lines, `#` flagging.

R2R publishes the products as GeoCSV; r2rnav is the format NavManager wrote them in
(`doc/fileformat/format-r2rnav.txt`), written here only for R2R's users, by `r2r run --r2rnav`
(#146): `<CRUISE>_bestres|1min|control.r2rnav`, with the GeoCSV products' columns
(`geocsv.PRODUCTS`) and, as R2R writes them, a flagged bestres record as `#` and time through
antenna height. Values are written through `quantities`, so floats keep the receiver's precision
across the whole fileset rather than NavManager's per-record trimming.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nav_qa_processing import geocsv
from nav_qa_processing import quantities as q
from nav_qa_processing.filenames import report_stem
from nav_qa_processing.qa import NavQAResult

FORMAT_URI = "http://get.rvdata.us/format/100002/format-r2rnav.txt"

# Column names by the number of columns in an r2rnav file
_LAYOUTS = {
    9: ("time", "lon", "lat", "quality", "nsat", "hdop", "alt", "sog", "cog"),
    7: ("time", "lon", "lat", "quality", "nsat", "hdop", "alt"),  # navcopy's pre-QC output
    5: ("time", "lon", "lat", "sog", "cog"),
    3: ("time", "lon", "lat"),
}


def write(
    path: Path,
    frame: pd.DataFrame,
    columns: dict[str, q.Quantity],
    decimals: dict[str, int],
    flagged: np.ndarray | None = None,
    header: bool = True,
    created: datetime | None = None,
    flagged_columns: int = 0,
) -> None:
    """Writes `frame[columns]` as r2rnav, with `#` before each `flagged` record, which has only
    its first `flagged_columns` fields when that's set.

    `decimals` gives the receiver's precision for the columns whose quantity takes it.
    """
    lines = []
    if header:
        created = created or datetime.now(UTC)
        lines += [
            "// " + "\t".join(quantity.label for quantity in columns.values()),
            f"// More detailed information may be found here: {FORMAT_URI}",
            f"// Created {q.format_datetime(created, 0)}",
        ]
    text_columns = [
        [quantity.format(value, decimals.get(name)) for value in frame[name]]
        for name, quantity in columns.items()
    ]
    marks = flagged if flagged is not None else np.zeros(len(frame), dtype=bool)
    short = flagged_columns or len(columns)
    lines += [
        "#" + "\t".join(values[:short]) if mark else "\t".join(values)
        for mark, values in zip(marks, zip(*text_columns, strict=True), strict=True)
    ]
    path.write_text("\n".join(lines) + "\n" if lines else "")


def file_name(stem: str, product: geocsv.Product) -> str:
    """e.g. SP2613_bestres.r2rnav"""
    return f"{report_stem(stem)}_{product.name}.r2rnav"


def paths(output: Path, stem: str) -> list[Path]:
    """The three r2rnav products write_products writes"""
    return [Path(output) / file_name(stem, p) for p in geocsv.PRODUCTS.values()]


def write_products(result: NavQAResult, output: Path) -> list[Path]:
    """bestres, 1min and control as r2rnav, with the GeoCSV products' columns and the run's
    time as their creation date
    """
    frames = {
        geocsv.BESTRES.name: result.bestres,
        geocsv.ONE_MINUTE.name: result.one_minute,
        geocsv.CONTROL.name: result.control,
    }
    Path(output).mkdir(parents=True, exist_ok=True)
    written = []
    for name, frame in frames.items():
        product = geocsv.PRODUCTS[name]
        path = Path(output) / file_name(result.config.stem, product)
        flagged = frame["flags"].to_numpy() != 0 if "flags" in frame else None
        write(
            path,
            frame,
            dict(product.columns),
            result.nav.decimals,
            flagged=flagged,
            created=result.finished,
            flagged_columns=product.flagged_columns,
        )
        written.append(path)
    return written


def read(path: Path) -> pd.DataFrame:
    """An r2rnav file as a DataFrame: the columns of its layout, plus `flagged`"""
    rows, flagged = [], []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("//"):
            continue
        flagged.append(line.startswith("#"))
        rows.append(line.lstrip("#").split("\t"))
    width = len(rows[0]) if rows else 3
    if width not in _LAYOUTS:
        raise ValueError(f"{path}: {width} columns is not an r2rnav layout")
    frame = pd.DataFrame(rows, columns=_LAYOUTS[width])
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    for name in frame.columns[1:]:
        frame[name] = pd.to_numeric(frame[name].replace("NAN", np.nan))
    frame["flagged"] = flagged
    return frame
