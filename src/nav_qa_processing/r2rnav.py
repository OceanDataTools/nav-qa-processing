"""Legacy r2rnav files (R2R format 100002): tab-separated, 3 `//` header lines, `#` flagging.

GeoCSV is the primary product format. r2rnav is kept as a secondary output and to compare with
NavManager's intermediate files. Values are written through `quantities`, so floats keep the
receiver's precision across the whole fileset rather than NavManager's per-record trimming.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nav_qa_processing import quantities as q

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
) -> None:
    """Writes `frame[columns]` as r2rnav, with `#` before each `flagged` record.

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
    lines += [
        ("#" if mark else "") + "\t".join(values)
        for mark, values in zip(marks, zip(*text_columns, strict=True), strict=True)
    ]
    path.write_text("\n".join(lines) + "\n" if lines else "")


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
