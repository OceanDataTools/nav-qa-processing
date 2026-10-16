"""Report themes: the page, fonts, colors, logo and footer of the PDF, its figures and the
notebook. Without one, the reports have the standard look.

A theme is a ``[theme]`` table in a config TOML, or a theme file passed with ``--theme`` (the
same keys at its top level). The config's table applies over the file's, field by field::

    page_size = "A4"            # "letter" (default) or "A4"
    margin = 0.6                # [in]
    font = "fonts/Inter.ttf"    # TTF; default DejaVu Sans, which ships with matplotlib
    bold_font = "fonts/Inter-Bold.ttf"
    font_scale = 1.0            # PDF text sizes
    figure_font_scale = 1.0     # figure text sizes
    logo = "logo.png"           # PNG or JPEG, beside the report title
    organization = "Scripps Institution of Oceanography"   # in the footer
    footer = ""                 # footer text; default the report title
    palette = "colorblind"      # "default" or "colorblind", then [colors] on top

    [colors]                    # [theme.colors] in a config TOML
    primary = "#1f5f8b"

Relative paths are relative to the TOML file. Ported from sbe-qa-processing's themes, with the
same keys; the colors name what they draw in the nav reports.
"""

import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING

from nav_qa_processing.config import toml_value

if TYPE_CHECKING:
    from nav_qa_processing.config import QAConfig

PAGE_SIZES = ("letter", "A4")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Colors:
    """Report colors, as #rrggbb"""

    primary: str = "#1f5f8b"  # the good track and its values
    secondary: str = "#d1495b"  # flagged fixes, limits, the expected area
    status_pass: str = "#d8f0df"  # check status backgrounds
    status_warn: str = "#fff1bf"
    status_fail: str = "#f6d0cc"
    status_info: str = "#eef2f7"
    status_na: str = "#f2f2f2"
    table_header: str = "#e3e8ef"
    table_grid: str = "#9aa4b2"
    muted_text: str = "#555555"  # small print
    footer_text: str = "#666666"
    land: str = "#ece6d6"
    water: str = "#dbe9f4"
    coast: str = "#7a7a7a"
    # Rating banners, for integrations that rate (R2R's G/Y/R)
    rating_green: str = "#2e8b57"
    rating_yellow: str = "#e0b000"
    rating_red: str = "#c0392b"

    @property
    def status(self) -> dict[str, str]:
        """By check status (pass/warn/fail/info/n/a)"""
        return {
            "pass": self.status_pass,
            "warn": self.status_warn,
            "fail": self.status_fail,
            "info": self.status_info,
            "n/a": self.status_na,
        }

    @property
    def rating(self) -> dict[str, str]:
        """By rating letter (G/Y/R)"""
        return {"G": self.rating_green, "Y": self.rating_yellow, "R": self.rating_red}


# Okabe and Ito's colors (https://jfly.uni-koeln.de/color/), which stay distinct with the
# common color vision deficiencies; status backgrounds are their light tints
COLORBLIND = Colors(
    primary="#0072b2",
    secondary="#d55e00",
    status_pass="#cde5f4",
    status_warn="#fbeaa8",
    status_fail="#f5cdb3",
    rating_green="#0072b2",
    rating_yellow="#e69f00",
    rating_red="#d55e00",
)
PALETTES = {"default": Colors(), "colorblind": COLORBLIND}


@dataclass(frozen=True)
class Theme:
    page_size: str = "letter"
    margin: float = 0.6  # [in]
    font: Path | None = None  # TTF; None for DejaVu Sans, which ships with matplotlib
    bold_font: Path | None = None
    font_scale: float = 1.0  # PDF text sizes
    figure_font_scale: float = 1.0  # figure text sizes
    logo: Path | None = None
    organization: str = ""
    footer: str = ""  # default: the report title
    palette: str = "default"
    colors: Colors = field(default_factory=Colors)


DEFAULT_THEME = Theme()
_PATHS = ("font", "bold_font", "logo")
_NUMBERS = ("margin", "font_scale", "figure_font_scale")
_KEYS = {f.name for f in fields(Theme)}
_COLOR_KEYS = {f.name for f in fields(Colors)}


class ThemeError(ValueError):
    """A theme setting that can't be used"""


def _check_table(table: dict, where: str) -> None:
    unknown = set(table) - _KEYS
    if unknown:
        raise ThemeError(f"{where}: unknown theme settings: {', '.join(sorted(unknown))}")
    colors = table.get("colors", {})
    if not isinstance(colors, dict):
        raise ThemeError(f"{where}: colors must be a table")
    unknown = set(colors) - _COLOR_KEYS
    if unknown:
        raise ThemeError(f"{where}: unknown theme colors: {', '.join(sorted(unknown))}")
    for name, value in colors.items():
        if not isinstance(value, str) or not _HEX_COLOR.match(value):
            raise ThemeError(f"{where}: colors.{name} = {value!r} isn't a #rrggbb color")
    if table.get("page_size", "letter") not in PAGE_SIZES:
        raise ThemeError(f"{where}: page_size must be one of {', '.join(PAGE_SIZES)}")
    if table.get("palette", "default") not in PALETTES:
        raise ThemeError(f"{where}: palette must be one of {', '.join(PALETTES)}")
    for name in _NUMBERS:
        value = table.get(name, 1.0)
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise ThemeError(f"{where}: {name} must be a positive number")
    for name in _PATHS:
        if name in table and not Path(table[name]).is_file():
            raise ThemeError(f"{where}: {name} {table[name]} doesn't exist")


def theme_table(table: dict, relative_to: Path | None, where: str) -> dict:
    """A checked [theme] table, with its file paths made absolute"""
    table = dict(table)
    for name in _PATHS:
        if name in table:
            path = Path(table[name]).expanduser()
            if not path.is_absolute() and relative_to is not None:
                path = relative_to / path
            table[name] = str(path.resolve())
    _check_table(table, where)
    return table


def apply_table(base: Theme, table: dict) -> Theme:
    """base with a checked [theme] table's settings on top. A palette replaces the colors;
    [colors] then changes single colors
    """
    settings = {k: v for k, v in table.items() if k != "colors"}
    for name in _PATHS:
        if name in settings:
            settings[name] = Path(settings[name])
    for name in _NUMBERS:
        if name in settings:
            settings[name] = float(settings[name])
    colors = PALETTES[settings["palette"]] if "palette" in settings else base.colors
    return replace(base, **settings, colors=replace(colors, **table.get("colors", {})))


def load_theme(path: Path | str) -> Theme:
    """A theme file: the [theme] table's settings at its top level"""
    path = Path(path)
    try:
        with open(path, "rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ThemeError(f"{path}: {error}") from error
    return apply_table(DEFAULT_THEME, theme_table(data, path.parent, str(path)))


def resolve_theme(theme_file: Path | str | None, config: "QAConfig | None" = None) -> Theme:
    """The theme file's settings (or the default), then the config's [theme] table"""
    base = load_theme(theme_file) if theme_file else DEFAULT_THEME
    return apply_table(base, config.theme) if config is not None and config.theme else base


def theme_toml(table: dict) -> list[str]:
    """A config's [theme] table as TOML lines, or nothing"""
    if not table:
        return []
    lines = ["", "[theme]"]
    lines += [f"{k} = {toml_value(v)}" for k, v in table.items() if k != "colors"]
    if table.get("colors"):
        lines += ["", "[theme.colors]"]
        lines += [f"{k} = {toml_value(v)}" for k, v in table["colors"].items()]
    return lines
