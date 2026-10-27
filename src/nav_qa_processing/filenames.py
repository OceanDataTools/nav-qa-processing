"""File names made from cruise IDs (#154).

A config TOML can set the cruise ID, and a feed's `[[nav]] name`, to anything, but the outputs
are named after them (`<CRUISE>_bestres.geoCSV`, `<CRUISE>_plots/`, `<CRUISE>_qa_report.pdf`,
R2R's `<CRUISE>_<FILESET>_qa.2.0.<time>.xml`, `<CRUISE>.toml`, ...). `safe_filename` makes an
ID into one file name that's valid on Linux, macOS and Windows, so a / or .. can't move the
outputs out of the output directory and Windows can write every name. The ID itself stays as
given wherever it's shown (the PDF, the notebook, the GeoCSV headers, the R2R XML). Ported from
sbe-qa-processing.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Path separators, the other characters Windows forbids in file names, and control characters
UNSAFE_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Names Windows reserves for devices, with or without an extension (CON, CON.toml, ...)
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}

_warned: set[str] = set()


def safe_filename(name: str) -> str:
    """name as a single file name valid on every platform: unsafe characters become _,
    trailing dots and spaces (which Windows drops) are removed, and an empty, dots-only or
    Windows-reserved name gets a leading _
    """
    safe = UNSAFE_CHARACTERS.sub("_", name).rstrip(". ")
    if not safe.strip(".") or safe.split(".")[0].upper() in WINDOWS_RESERVED:
        safe = "_" + safe
    return safe


def report_stem(identifier: str) -> str:
    """The file name prefix for reports named after identifier, warning (once per identifier)
    when it isn't usable as is
    """
    stem = safe_filename(identifier)
    if stem != identifier and identifier not in _warned:
        _warned.add(identifier)
        logger.warning(
            "%r isn't a valid file name on every platform; the files are named %s instead",
            identifier,
            stem,
        )
    return stem
