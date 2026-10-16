"""QA configuration. Every field is optional: the QA runs without a config at all.

A config TOML looks like (see "Configuration TOMLs" in docs/architecture.md)::

    [cruise]
    id = "SP2613"                 # the reports' title; default: the fileset directory's name
    name = "..."
    vessel_name = "Robert Gordon Sproul"
    depart_date = 2026-07-09      # the reader's file window and QC's DATE_RANGE
    arrive_date = 2026-07-09
    doi = "doi:10.7284/911454"    # GeoCSV source_event

    [cruise.extent]               # expected area
    westernmost = -117.38
    easternmost = -117.22
    southernmost = 32.59
    northernmost = 32.71

    [fileset]
    doi = "doi:10.7284/169851"    # GeoCSV source_dataset
    manifest = "md5.txt"          # checksums for a plain directory; a bag uses its own

    [nav]                         # the primary GNSS feed
    format = "nav29"              # a format ID (`nav-qa-processing formats`)
    files = "data/*.txt"          # glob within the fileset

    [provenance]
    contact_institution = "..."
    contact_email = "..."

    [geocsv]
    attribution = "..."           # GeoCSV attribution

    [thresholds]                  # overrides of Thresholds
    max_accel = 1.0

    [theme]                       # the reports' look (theme.py); over a --theme file
    page_size = "A4"

Tables and keys only the integrations use (R2R's IDs and ports, the site TOML's [openvdm]) are
ignored. Threshold defaults are NavManager's (`include/globals.inc.php`, `navtools.inc.php`,
`navqc.inc.php`) except where noted.
"""

import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from pathlib import Path

from nav_qa_processing.readable import Readable, latitude, longitude


@dataclass
class Extent(Readable):
    """Cruise bounding box in decimal degrees (longitude east positive). As in R2R's reports, a
    box across the antimeridian has westernmost > easternmost (e.g. 178 to -178)
    """

    westernmost: float
    easternmost: float
    southernmost: float
    northernmost: float

    @property
    def crosses_antimeridian(self) -> bool:
        return self.westernmost > self.easternmost

    def contains(self, latitude: float, longitude: float, tolerance: float = 0.0) -> bool:
        """Whether the point is in the box, or within `tolerance` [degree] of it"""
        if not self.southernmost - tolerance <= latitude <= self.northernmost + tolerance:
            return False
        west, east = self.westernmost - tolerance, self.easternmost + tolerance
        if self.crosses_antimeridian:
            return longitude >= west or longitude <= east
        return west <= longitude <= east

    def __str__(self) -> str:
        across = " (across the antimeridian)" if self.crosses_antimeridian else ""
        return (
            f"{latitude(self.southernmost)} to {latitude(self.northernmost)}, "
            f"{longitude(self.westernmost)} to {longitude(self.easternmost)}{across}"
        )


@dataclass(frozen=True)
class Thresholds(Readable):
    """QC and QA limits. Speed isn't a QC rule: a vessel's top speed varies and currents change
    speed over ground, so acceleration is the better indicator of a bad fix; `max_speed` is for
    the QA statistics
    """

    # -- QA statistics --
    max_speed: float = 8.7  # [m s-1] a speed above this is unreasonable
    max_gap: float = 300.0  # [s] a step this long or longer is a gap
    # -- Core checks: pass at or within these, warn otherwise. The defaults equal R2R's green
    #    bounds; R2R's own G/Y/R ratings belong to the R2R integration --
    min_completeness: float = 95.0  # [percent]
    max_longest_gap: float = 900.0  # [s]
    max_out_of_sequence: float = 5.0  # [percent] of records
    max_bad_quality: float = 5.0  # [percent] of records reporting a quality indicator
    max_unreasonable_speeds: float = 5.0  # [percent] of speeds
    max_unreasonable_accelerations: float = 5.0  # [percent] of accelerations
    # -- QC --
    max_accel: float = 1.0  # [m s-2]
    # Acceleration at a fix uses velocities over at least this long before and after it (not in
    # NavManager): a fix displaced by d metres shows as ~2 d / time_base² m s-2, so 2 s ignores
    # GNSS noise at high sample rates but still catches jumps of 2 m or more
    accel_time_base: float = 2.0  # [s]
    gps_start: date = date(1994, 3, 1)  # valid GPS dates
    gps_end: date = date(2034, 12, 31)
    date_margin_days: float = 30.0  # [days] allowed outside the cruise dates
    max_time_step_days: float = 30.0  # [days] largest step forward in time
    # NMEA GGA fix quality: 0 invalid, 6 dead reckoning, 7 manual input, 8 simulator
    min_quality: int = 1
    max_quality: int = 5
    min_satellites: int = 4
    max_satellites: int = 50
    max_hdop: float = 50.0
    max_altitude: float = 500.0  # [m], magnitude
    stale_speed: float = 0.25  # [m s-1] median speed above which a repeated position is stale
    stale_window: int = 10  # [speeds] for that median

    def __str__(self) -> str:
        defaults = Thresholds()
        lines = []
        for f in fields(self):
            value, default = getattr(self, f.name), getattr(defaults, f.name)
            unit = f" {THRESHOLD_UNITS[f.name]}" if THRESHOLD_UNITS.get(f.name) else ""
            changed = "" if value == default else f" (default {_threshold(default)})"
            lines.append(f"{f.name}: {_threshold(value)}{unit}{changed}")
        return "\n".join(lines)


THRESHOLD_UNITS = {
    "max_speed": "m s-1",
    "max_gap": "s",
    "min_completeness": "%",
    "max_longest_gap": "s",
    "max_out_of_sequence": "%",
    "max_bad_quality": "%",
    "max_unreasonable_speeds": "%",
    "max_unreasonable_accelerations": "%",
    "max_accel": "m s-2",
    "accel_time_base": "s",
    "date_margin_days": "days",
    "max_time_step_days": "days",
    "max_altitude": "m",
    "stale_speed": "m s-1",
}


def _threshold(value) -> str:
    return value.isoformat() if isinstance(value, date) else f"{value:g}"


@dataclass(kw_only=True)
class QAConfig(Readable):
    """What the core QA uses. Every field is optional"""

    cruise_id: str = ""  # the reports' title; run_qa defaults it to the fileset directory's name
    cruise_name: str = ""
    cruise_pi: str = ""
    cruise_location: str = ""
    vessel_name: str = ""
    contact_institution: str = ""
    contact_email: str = ""
    # Expected area and dates. The dates set the reader's file window and QC's DATE_RANGE
    extent: Extent | None = None
    depart_date: date | None = None
    arrive_date: date | None = None
    # GeoCSV header: source_event, source_dataset and attribution
    cruise_doi: str = ""
    fileset_doi: str = ""
    attribution: str = ""
    # The primary GNSS feed: a format ID, and a glob of its files within the fileset
    nav_format: str = ""
    nav_files: str = ""
    # Checksum manifest for a plain directory (e.g. OpenVDM's MD5 summary): "<md5> <path>" lines,
    # with paths relative to the manifest's directory. A BagIt bag uses its own manifest-md5.txt
    manifest_path: Path | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    # The [theme] table, checked and with absolute paths (theme.py); applied over a --theme file
    theme: dict = field(default_factory=dict)

    @property
    def identifier(self) -> str:
        """The products' and reports' file name prefix"""
        return self.cruise_id

    def __str__(self) -> str:
        """The config as TOML, as load_config reads it"""
        return config_to_toml(self).rstrip("\n")


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def thresholds_from_table(table: dict) -> Thresholds:
    known = {f.name: f for f in fields(Thresholds)}
    unknown = set(table) - set(known)
    if unknown:
        raise ValueError(f"unknown thresholds: {', '.join(sorted(unknown))}")
    defaults = Thresholds()
    values = {
        k: as_date(v) if isinstance(getattr(defaults, k), date) else v for k, v in table.items()
    }
    return Thresholds(**values)


def load_config(path: Path | str) -> QAConfig:
    """A config TOML. Tables and keys only the integrations use are ignored"""
    from nav_qa_processing.theme import theme_table  # theme.py imports this module

    with open(path, "rb") as file:
        data = tomllib.load(file)
    cruise = data.get("cruise", {})
    fileset = data.get("fileset", {})
    nav = data.get("nav", {})
    provenance = data.get("provenance", {})
    manifest = fileset.get("manifest")
    return QAConfig(
        cruise_id=str(cruise.get("id", "")),
        cruise_name=cruise.get("name", ""),
        cruise_pi=cruise.get("pi", ""),
        cruise_location=cruise.get("location", ""),
        vessel_name=cruise.get("vessel_name", ""),
        contact_institution=provenance.get("contact_institution", ""),
        contact_email=provenance.get("contact_email", ""),
        extent=Extent(**cruise["extent"]) if "extent" in cruise else None,
        depart_date=as_date(cruise["depart_date"]) if "depart_date" in cruise else None,
        arrive_date=as_date(cruise["arrive_date"]) if "arrive_date" in cruise else None,
        cruise_doi=cruise.get("doi", ""),
        fileset_doi=fileset.get("doi", ""),
        attribution=data.get("geocsv", {}).get("attribution", ""),
        nav_format=nav.get("format", ""),
        nav_files=nav.get("files", ""),
        manifest_path=(Path(path).parent / manifest).resolve() if manifest else None,
        thresholds=thresholds_from_table(data.get("thresholds", {})),
        theme=theme_table(data.get("theme", {}), Path(path).parent, str(path)),
    )


def config_to_toml(config: QAConfig) -> str:
    """The config as TOML that load_config reads back, e.g. to snapshot what a run used"""
    from nav_qa_processing.theme import theme_toml  # theme.py imports this module

    cruise = {
        "id": config.cruise_id,
        "name": config.cruise_name,
        "pi": config.cruise_pi,
        "location": config.cruise_location,
        "vessel_name": config.vessel_name,
    }
    lines = ["[cruise]"] + [f"{k} = {toml_value(v)}" for k, v in cruise.items() if v]
    lines += [
        f"{which} = {value}"
        for which, value in (
            ("depart_date", config.depart_date),
            ("arrive_date", config.arrive_date),
        )
        if value is not None
    ]
    if config.cruise_doi:
        lines.append(f"doi = {toml_value(config.cruise_doi)}")
    if config.extent:
        lines += ["", "[cruise.extent]"]
        lines += [f"{k} = {getattr(config.extent, k)}" for k in fields_of(Extent)]
    fileset = {"doi": config.fileset_doi}
    if config.manifest_path:
        fileset["manifest"] = str(config.manifest_path)
    lines += _table("fileset", fileset)
    lines += _table("nav", {"format": config.nav_format, "files": config.nav_files})
    provenance = {k: getattr(config, k) for k in ("contact_institution", "contact_email")}
    lines += _table("provenance", provenance)
    lines += _table("geocsv", {"attribution": config.attribution})
    lines += thresholds_toml(config.thresholds)
    lines += theme_toml(config.theme)
    return "\n".join(lines) + "\n"


def _table(name: str, values: dict[str, str]) -> list[str]:
    """A TOML table of the non-empty values, or nothing"""
    values = {k: v for k, v in values.items() if v}
    if not values:
        return []
    return ["", f"[{name}]"] + [f"{k} = {toml_value(v)}" for k, v in values.items()]


def thresholds_toml(thresholds: Thresholds) -> list[str]:
    """A [thresholds] table of the values that differ from the defaults, or nothing"""
    defaults = Thresholds()
    changed = {
        f.name: getattr(thresholds, f.name)
        for f in fields(Thresholds)
        if getattr(thresholds, f.name) != getattr(defaults, f.name)
    }
    if not changed:
        return []
    return ["", "[thresholds]"] + [f"{k} = {_toml_scalar(v)}" for k, v in changed.items()]


def _toml_scalar(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def with_thresholds(config: QAConfig, **changes) -> QAConfig:
    """A copy of `config` with some thresholds changed"""
    return replace(config, thresholds=replace(config.thresholds, **changes))


def fields_of(cls) -> list[str]:
    return [f.name for f in fields(cls)]


def toml_value(value) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)
