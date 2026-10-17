"""R2R's cruise config: the core QAConfig plus what R2R's catalog knows about the cruise and the
fileset, for the QA 2.0 XML's provenance and filesetinfo.

The cruise TOML is a core config TOML with these added (see docs/architecture.md, "Configuration
TOMLs")::

    [cruise]
    id = "SP2613"              # required, as are the dates
    vessel_id = "32QU"         # R2R's vessel ID, the ICES code
    operator_id = "edu.ucsd.sio"
    scheduler_id = "org.unols"
    date_issued = "2026-07-22" # R2R's catalog dates, as R2R writes them
    date_modified = "2026-07-31T04:28:14Z"
    depart_date = 2026-07-09
    arrive_date = 2026-07-09

    [cruise.depart_port]       # and [cruise.arrive_port]
    name = "San Diego"
    port_id = "100055"
    country_id = "us"
    state_id = "ca"
    longitude = -117.175
    latitude = 32.71667

    [fileset]
    id = "169851"
    date_created = "2026-07-22"
    format_id = "100150"       # R2R's raw format ID, and its alias
    format_alias = "starc_raw"

    [fileset.device]           # the GNSS receiver as R2R catalogs it
    id = "101253"
    make_id = "com.trimble"
    model_id = "100542"
    model_name = "BX992"

R2R's device gives the core config's receiver (`nav_device`: make, model and location) unless
the cruise TOML has a [nav.device] of its own.

    [provenance]
    distro_type = "post-cruise"
    contact_institution_id = "us.rvdata"
"""

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from nav_qa_processing.config import (
    Extent,
    NavDevice,
    QAConfig,
    fields_of,
    load_config,
    loads_config,
    nav_device_toml,
    thresholds_toml,
    toml_value,
)
from nav_qa_processing.theme import theme_toml


@dataclass
class Port:
    name: str = ""
    port_id: str = ""
    country_id: str = ""
    state_id: str = ""
    longitude: float | None = None
    latitude: float | None = None


@dataclass
class Device:
    """The GNSS receiver as R2R catalogs it"""

    id: str = ""
    make_id: str = ""
    model_id: str = ""
    model_name: str = ""
    location_name: str = ""


@dataclass(kw_only=True)
class CruiseConfig(QAConfig):
    """A cruise as R2R catalogs it: the core config plus R2R's IDs, ports, device and provenance,
    with the cruise ID and dates required. The R2R tests and XML need it
    """

    cruise_id: str
    depart_date: date
    arrive_date: date
    fileset_id: str = ""  # R2R's, assigned after submission; optional
    vessel_id: str = ""
    operator_id: str = ""
    scheduler_id: str = ""
    date_issued: str = ""
    date_modified: str = ""
    depart_port: Port = field(default_factory=Port)
    arrive_port: Port = field(default_factory=Port)
    fileset_date_created: str = ""
    format_id: str = ""
    format_alias: str = ""
    device: Device = field(default_factory=Device)
    distro_type: str = "post-cruise"
    contact_institution_id: str = ""

    def __post_init__(self) -> None:
        if not self.nav_device:
            self.nav_device = nav_device_from_r2r(self.device)

    @property
    def identifier(self) -> str:
        """<cruise>_<fileset>, or <cruise>_gnss without an R2R fileset ID"""
        return f"{self.cruise_id}_{self.fileset_id or 'gnss'}"

    def __str__(self) -> str:
        """The cruise TOML, as load_cruise_config reads it"""
        return config_to_toml(self).rstrip("\n")


def nav_device_from_r2r(device: Device) -> NavDevice:
    """The core config's receiver from R2R's catalog entry: its make ID, model name and
    location
    """
    return NavDevice(make=device.make_id, model=device.model_name, location=device.location_name)


# What a CruiseConfig needs, from [cruise]
REQUIRED = ("id", "depart_date", "arrive_date")


def load_cruise_config(path: Path | str) -> CruiseConfig:
    """A cruise TOML, with the cruise ID and dates R2R needs"""
    with open(path, "rb") as file:
        data = tomllib.load(file)
    return _cruise_config(data, load_config(path), str(path))


def loads_cruise_config(text: str) -> CruiseConfig:
    """A cruise TOML's text, e.g. `config_to_toml`'s; relative paths are relative to the
    working directory
    """
    return _cruise_config(tomllib.loads(text), loads_config(text), "config")


def _cruise_config(data: dict, core: QAConfig, where: str) -> CruiseConfig:
    cruise = data.get("cruise", {})
    missing = [key for key in REQUIRED if key not in cruise]
    if missing:
        raise ValueError(f"{where}: R2R needs [cruise] {', '.join(missing)}")
    fileset = data.get("fileset", {})
    provenance = data.get("provenance", {})
    return CruiseConfig(
        **{name: getattr(core, name) for name in fields_of(QAConfig)},
        fileset_id=str(fileset.get("id", "")),
        vessel_id=cruise.get("vessel_id", ""),
        operator_id=cruise.get("operator_id", ""),
        scheduler_id=cruise.get("scheduler_id", ""),
        date_issued=str(cruise.get("date_issued", "")),
        date_modified=str(cruise.get("date_modified", "")),
        depart_port=Port(**cruise.get("depart_port", {})),
        arrive_port=Port(**cruise.get("arrive_port", {})),
        fileset_date_created=str(fileset.get("date_created", "")),
        format_id=str(fileset.get("format_id", "")),
        format_alias=fileset.get("format_alias", ""),
        device=Device(**{k: str(v) for k, v in fileset.get("device", {}).items()}),
        distro_type=provenance.get("distro_type", "post-cruise"),
        contact_institution_id=provenance.get("contact_institution_id", ""),
    )


def config_to_toml(config: CruiseConfig) -> str:
    """The config as cruise TOML"""
    lines = ["[cruise]"]
    for key, value in (
        ("id", config.cruise_id),
        ("name", config.cruise_name),
        ("pi", config.cruise_pi),
        ("location", config.cruise_location),
        ("vessel_id", config.vessel_id),
        ("vessel_name", config.vessel_name),
        ("operator_id", config.operator_id),
        ("scheduler_id", config.scheduler_id),
        ("date_issued", config.date_issued),
        ("date_modified", config.date_modified),
    ):
        if value:
            lines.append(f"{key} = {toml_value(value)}")
    lines += [f"depart_date = {config.depart_date}", f"arrive_date = {config.arrive_date}"]
    if config.cruise_doi:
        lines.append(f"doi = {toml_value(config.cruise_doi)}")
    if config.extent:
        lines += ["", "[cruise.extent]"]
        lines += [f"{k} = {getattr(config.extent, k)}" for k in fields_of(Extent)]
    for which in ("depart_port", "arrive_port"):
        lines += _table(f"cruise.{which}", getattr(config, which), Port)
    fileset = {
        "id": config.fileset_id,
        "doi": config.fileset_doi,
        "date_created": config.fileset_date_created,
        "format_id": config.format_id,
        "format_alias": config.format_alias,
        "manifest": str(config.manifest_path) if config.manifest_path else "",
    }
    fileset = {k: v for k, v in fileset.items() if v}
    if fileset:
        lines += ["", "[fileset]"] + [f"{k} = {toml_value(v)}" for k, v in fileset.items()]
    lines += _table("fileset.device", config.device, Device)
    nav = {"format": config.nav_format, "files": config.nav_files}
    nav = {k: v for k, v in nav.items() if v}
    if nav:
        lines += ["", "[nav]"] + [f"{k} = {toml_value(v)}" for k, v in nav.items()]
    if config.nav_device != nav_device_from_r2r(config.device):  # not just R2R's
        lines += nav_device_toml(config.nav_device)
    provenance = {
        k: getattr(config, k)
        for k in ("distro_type", "contact_institution", "contact_institution_id", "contact_email")
        if getattr(config, k)
    }
    lines += ["", "[provenance]"] + [f"{k} = {toml_value(v)}" for k, v in provenance.items()]
    if config.attribution:
        lines += ["", "[geocsv]", f"attribution = {toml_value(config.attribution)}"]
    lines += thresholds_toml(config.thresholds)
    lines += theme_toml(config.theme)
    return "\n".join(lines) + "\n"


def _table(name: str, value, cls) -> list[str]:
    values = {k: getattr(value, k) for k in fields_of(cls) if getattr(value, k) not in ("", None)}
    if not values:
        return []
    return ["", f"[{name}]"] + [f"{k} = {toml_value(v)}" for k, v in values.items()]
