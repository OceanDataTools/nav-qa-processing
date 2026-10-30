"""Runs the core QA from an OpenVDM post-transfer hook (ported from sbe-qa-processing).

OpenVDM (https://github.com/OceanDataTools/openvdm) runs ``postCollectionSystemTransfer`` hook
commands from ``openvdm.yaml`` after each collection system transfer. The cruise comes from
OpenVDM's ``api/warehouse/getCruiseConfig``: its ID, name, PI, location and dates; the warehouse
directory and MD5 summary file; each collection system transfer's and extra directory's
destination; and, from OpenVDM 2.17, the cruise bounding box (``cruiseExtent``).

What OpenVDM doesn't store comes from the per-ship site TOML (`SiteConfig`): above all the GNSS
feed, ``[nav] format`` and ``files`` (e.g. ``raw/POSMV_GGA-*.txt`` within an ``OpenRVDAS``
collection system), and the GeoCSV attribution. OpenVDM 2.17 keeps the vessel's name and
contact in ``openvdm.yaml``'s ``vessel`` block, which it copies into each cruise's
``cruise_config.json``; the hook reads it from ``openvdm.yaml`` when it has that file, else from
``cruise_config.json``. The site TOML's values win; OpenVDM's fill the fields it leaves blank (#112),
and `site-config` offers them as defaults. Its R2R IDs aren't used: the hook writes
the core reports, not R2R's.

The hook sets OpenVDM 2.17's ``cruiseExtent`` from the fixes that pass QC (`set_cruise_extent`),
with the worker key (``workerApiKey``) from ``openvdm.yaml``: a box from the QC'd fixes, which
leave out jumps to 0, 0 and stretches the ship couldn't reach, is better than OpenVDM's own from
the dashboard's unchecked positions. The area check is then n/a, since the extent is the track's.
With ``--no-set-extent``, without a worker key, or with OpenVDM before 2.17, the hook checks the
fixes against ``cruiseExtent``, else the site TOML's ``[extent]``.

Each run assesses the whole cruise so far: 20 days at 2 Hz (3.5 M fixes) takes minutes, longer
than OpenVDM's 5-minute transfer interval, and OpenVDM runs its post hooks one at a time. With
``--min-interval`` (e.g. ``6h``) the post-transfer hook skips a feed whose reports are newer than
that (`written_within`); the same command, without it, as a ``preFinalizeCurrentCruise`` hook
writes the final reports on the whole cruise, which OpenVDM waits for before finalizing.

A past cruise, or any cruise without OpenVDM's API, comes from the ``cruise_config.json`` OpenVDM
writes into each cruise's directory (``--cruise-config``). `load_cruise_json` gives it the
API's shape: the file is the API's cruise config without its warehouse block, which it rebuilds
from the file's directory (the cruise's, named after it as OpenVDM names it; the warehouse is
its parent) and the MD5 summary's name the file keeps at its top level. From there it's
resolved as the current cruise is, the vessel from the same file. OpenVDM's cruise extent isn't
set for it (that's the current cruise's), and OpenVDM takes the reports into the cruise's MD5
summary only if its warehouse has the cruise there (`queue_md5_update`).

The OpenVDM API is read over HTTP, so this runs in its own environment, not OpenVDM's venv.
Reading ``openvdm.yaml`` needs pyyaml, the ``openvdm`` extra.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from nav_qa_processing.config import (
    CORE_KEYS,
    INTEGRATION_KEYS,
    KEY,
    Extent,
    NavDevice,
    QAConfig,
    Thresholds,
    check_keys,
    config_to_toml,
    fields_of,
    nav_device_from_table,
    nav_device_toml,
    nav_tables,
    thresholds_from_table,
    thresholds_toml,
    toml_value,
)
from nav_qa_processing.formats.catalog import canonical_id
from nav_qa_processing.network import reaching
from nav_qa_processing.theme import theme_table, theme_toml

logger = logging.getLogger(__name__)

DEFAULT_OPENVDM_CONFIG = Path("/opt/openvdm/server/etc/openvdm.yaml")
API_TIMEOUT = 30  # [s]
EXTENT_DECIMALS = 6  # [degree] about 11 cm; as OpenVDM rounds the extents it sets
MD5_UPDATE_TIMEOUT = 120  # [s] to queue the job, not to hash the files


class OpenVDMError(RuntimeError):
    """OpenVDM's or the site's configuration doesn't support running the QA (e.g. a missing
    directory, or no nav format)
    """


@dataclass
class SiteFeed:
    """A position sensor's feed the hook assesses: a [nav] table, or one of several [[nav]]"""

    name: str = ""  # required with several feeds; a non-primary feed's outputs carry it
    primary: bool = True  # sets cruiseExtent, and its outputs are named after the cruise alone
    format: str = ""
    files: str = ""
    device: NavDevice = field(default_factory=NavDevice)
    transfer: str = ""  # the collection system transfer it's in; any, when blank

    @property
    def label(self) -> str:
        return self.name or "[nav]"


@dataclass
class SiteConfig:
    """Per-ship settings OpenVDM doesn't store, from an optional TOML. From OpenVDM 2.17, the
    vessel name and contact are also in openvdm.yaml, which fills these where they're blank::

    [vessel]
    name = "Roger Revelle"

    [nav]                     # the primary GNSS feed within the collection system
    format = "nav33"
    files = "raw/POSMV_GGA-*.txt"

    [nav.device]              # the receiver or INS; every key optional
    make = "Applanix"
    model = "POS MV"
    location = "main lab"

    [extent]                  # with --no-set-extent, when OpenVDM has no cruiseExtent
    westernmost = -125.0
    easternmost = -115.0
    southernmost = 30.0
    northernmost = 36.0

    [openvdm]
    output_extra_directory = "NAV_QA"

    [provenance]
    contact_institution = "Scripps Institution of Oceanography"
    contact_email = "ship-tech@example.org"

    [geocsv]
    attribution = "R/V Roger Revelle, Scripps Institution of Oceanography"

    [thresholds]              # as in a config TOML

    [theme]                   # as in a config TOML; paths relative to this file

    Several position sensors are [[nav]] tables, as in a config TOML, each optionally naming its
    collection system transfer (#58)::

    [[nav]]
    name = "posmv"
    primary = true
    transfer = "OpenRVDAS"    # the hook runs the feeds of its TRANSFER, and those without one
    format = "nav33"
    files = "raw/POSMV_GGA-*.txt"

    [[nav]]
    name = "seapath"
    transfer = "OpenRVDAS"
    format = "nav33"
    files = "raw/SEAPATH_GGA-*.txt"
    """

    vessel_name: str = ""
    feeds: list[SiteFeed] = field(default_factory=lambda: [SiteFeed()])  # the primary first
    extent: Extent | None = None
    output_extra_directory: str = "NAV_QA"
    contact_institution: str = ""
    contact_email: str = ""
    attribution: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)
    theme: dict = field(default_factory=dict)  # the [theme] table, as QAConfig.theme

    @property
    def primary(self) -> SiteFeed:
        return self.feeds[0]

    def feeds_for(self, transfer: str) -> list[SiteFeed]:
        """The feeds a transfer's hook assesses: those naming it, and those naming none"""
        return [feed for feed in self.feeds if feed.transfer in ("", transfer)]


# The site TOML's keys (#123): any other is an error naming where it belongs
# The tables it shares with the config TOML are core's, so a key added there is accepted here
# too (#134); [vessel], the top-level [extent] and a feed's transfer are the site's own
SITE_KEYS = {
    "vessel": {"name": KEY},
    "nav": CORE_KEYS["nav"] | INTEGRATION_KEYS["nav"],
    "extent": CORE_KEYS["cruise"]["extent"],
    "openvdm": INTEGRATION_KEYS["openvdm"],
    **{table: CORE_KEYS[table] for table in ("provenance", "geocsv", "thresholds", "theme")},
}


# A site TOML describes the ship, so a cruise table in it is a mistake worth explaining
SITE_NOTES = {
    "cruise": "a site TOML has no cruise: the hook gets each cruise's ID, name, chief "
    "scientist (cruisePI), location and dates from OpenVDM; the vessel's name is [vessel] name",
}


def load_site_config(path: Path | str | None) -> SiteConfig:
    if path is None:
        return SiteConfig()
    with open(path, "rb") as file:
        data = tomllib.load(file)
    check_keys(data, SITE_KEYS, str(path), SITE_NOTES)
    vessel, openvdm = data.get("vessel", {}), data.get("openvdm", {})
    provenance = data.get("provenance", {})
    defaults = SiteConfig()
    feeds = [
        SiteFeed(
            name=nav.get("name", ""),
            primary=nav["primary"],
            format=canonical_id(nav.get("format", "")),
            files=nav.get("files", ""),
            device=nav_device_from_table(nav.get("device", {})),
            transfer=nav.get("transfer", ""),
        )
        for nav in nav_tables(data, str(path))
    ]
    return SiteConfig(
        vessel_name=vessel.get("name", ""),
        feeds=feeds,
        extent=Extent(**data["extent"]) if "extent" in data else None,
        output_extra_directory=openvdm.get(
            "output_extra_directory", defaults.output_extra_directory
        ),
        contact_institution=provenance.get("contact_institution", ""),
        contact_email=provenance.get("contact_email", ""),
        attribution=data.get("geocsv", {}).get("attribution", ""),
        thresholds=thresholds_from_table(data.get("thresholds", {})),
        theme=theme_table(data.get("theme", {}), Path(path).parent, str(path)),
    )


def site_config_to_toml(site: SiteConfig) -> str:
    """The site config as TOML that load_site_config reads back. Blank fields are written
    commented out, so the file shows everything that can be set
    """

    def entry(key: str, value, comment: str = "") -> str:
        line = f"{key} = {toml_value(value)}" if value else f'# {key} = ""'
        return f"{line:<40} # {comment}" if comment else line

    lines = [
        "# Per-ship settings for the OpenVDM hook (nav-qa-processing openvdm --site-config ...).",
        "# OpenVDM supplies the cruise ID, name, PI, location and dates. From OpenVDM 2.17,",
        "# openvdm.yaml's vessel block has the vessel name and contact too; these win, and",
        "# openvdm.yaml fills any left blank.",
        "",
        "[vessel]",
        entry("name", site.vessel_name),
        "",
        "# The primary GNSS feed: its format (`nav-qa-processing formats`) and a glob of its",
        "# files within the collection system's directory. Several position sensors are",
        "# [[nav]] tables, each with a name (and optionally its transfer), one primary = true",
    ]
    several = len(site.feeds) > 1
    for feed in site.feeds:
        lines += ["[[nav]]" if several else "[nav]"]
        if feed.name:
            lines.append(entry("name", feed.name))
        if several:
            lines.append(f"primary = {'true' if feed.primary else 'false'}")
        if feed.transfer:
            lines.append(entry("transfer", feed.transfer))
        lines += [entry("format", feed.format, "required"), entry("files", feed.files)]
        device = nav_device_toml(feed.device)
        if device:
            lines += device
        elif not several:
            lines += [
                "",
                "# The receiver or INS, for the reports; every key optional",
                "# [nav.device]",
            ]
            lines += [f'# {k} = ""' for k in fields_of(NavDevice)]
        lines.append("")
    lines += [
        "# Expected cruise area, used when the hook doesn't set OpenVDM's cruise extent",
        "# (--no-set-extent) and OpenVDM has none. westernmost > easternmost is a box across the",
        "# antimeridian",
    ]
    edges = fields_of(Extent)
    if site.extent:
        lines += ["[extent]"] + [f"{k} = {getattr(site.extent, k)}" for k in edges]
    else:
        lines += ["# [extent]"] + [f"# {k} = 0.0" for k in edges]
    lines += [
        "",
        "[openvdm]",
        entry("output_extra_directory", site.output_extra_directory, "where reports go"),
        "",
        "[provenance]",
        entry("contact_institution", site.contact_institution),
        entry("contact_email", site.contact_email),
        "",
        "# The GeoCSV products' attribution",
        "[geocsv]",
        entry("attribution", site.attribution),
    ]
    # max_speed is always written: the vessel's own top speed matters to every ship (#113)
    others = [
        line for line in thresholds_toml(site.thresholds)[2:] if not line.startswith("max_speed")
    ]
    thresholds = [
        "",
        "# QA thresholds, as in a config TOML. max_speed is the vessel's top speed [m s-1]: a",
        "# step faster than it counts as an unreasonable speed (1 knot is 0.514 m s-1)",
        "[thresholds]",
        f"max_speed = {site.thresholds.max_speed}",
        *others,
    ]
    theme = theme_toml(site.theme)
    if not theme:
        theme = ["", "# The reports' theme, as in a config TOML; e.g.:"]
        theme += ["# [theme]", '# page_size = "A4"', '# palette = "colorblind"']
    return "\n".join(lines + thresholds + theme) + "\n"


def load_openvdm_yaml(openvdm_config: Path | str) -> dict:
    try:
        import yaml
    except ImportError:
        raise OpenVDMError(
            "reading openvdm.yaml needs pyyaml, the openvdm extra: uv sync --extra openvdm, or "
            "pip install 'nav-qa-processing[openvdm]'"
        ) from None
    try:
        with open(openvdm_config) as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError:
        # e.g. OpenVDM installed outside /opt (its installer's INSTALL_ROOT)
        raise OpenVDMError(
            f"no openvdm.yaml at {openvdm_config}: give its path with --openvdm-config, or a "
            "cruise's cruise_config.json with --cruise-config"
        ) from None


def site_root_from_config(openvdm_config: Path | str | dict) -> str:
    """OpenVDM's web root (``siteRoot`` in openvdm.yaml, or its loaded contents), ending in a
    slash
    """
    data = (
        openvdm_config if isinstance(openvdm_config, dict) else load_openvdm_yaml(openvdm_config)
    )
    site_root = data["siteRoot"]
    return site_root if site_root.endswith("/") else site_root + "/"


# OpenVDM 2.17's vessel block: the SiteConfig field each of its settings fills. Its r2r IDs
# aren't used
VESSEL_SETTINGS = {
    "vessel_name": ("name",),
    "contact_institution": ("contact", "institution"),
    "contact_email": ("contact", "email"),
}


@dataclass
class OpenVDMVessel:
    """The vessel settings OpenVDM has, by SiteConfig field, and where they came from"""

    settings: dict[str, str] = field(default_factory=dict)
    source: str = "none"  # "openvdm.yaml", the cruise config file's name, or "none"


def vessel_settings(block, source: str) -> OpenVDMVessel:
    """The set fields of a ``vessel`` block. Empty and missing values are unset"""
    settings = {}
    for name, path in VESSEL_SETTINGS.items():
        value = block
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and not isinstance(value, dict) and str(value).strip():
            settings[name] = str(value).strip()
    return OpenVDMVessel(settings, source)


def cruise_vessel(cruise_dir: Path, config_filename: str) -> OpenVDMVessel:
    """The vessel block saved in the cruise's cruise_config.json, if any"""
    path = cruise_dir / config_filename
    try:
        block = json.loads(path.read_text()).get("vessel")
    except FileNotFoundError:
        return OpenVDMVessel()
    except (OSError, ValueError, AttributeError) as error:
        logger.warning("can't read the vessel settings from %s: %s", path, error)
        return OpenVDMVessel()
    return vessel_settings(block, config_filename) if block else OpenVDMVessel()


def load_cruise_json(path: Path | str) -> dict:
    """A cruise's config from the cruise_config.json OpenVDM writes into its directory: any cruise,
    past or current, without OpenVDM's API (--cruise-config)
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise OpenVDMError(f"can't read the cruise config {path}: {error}") from None
    if not isinstance(data, dict) or not data.get("cruiseID"):
        raise OpenVDMError(f"{path} isn't an OpenVDM cruise config: it has no cruiseID")
    cruise_dir = path.absolute().parent
    if cruise_dir.name != data["cruiseID"]:
        raise OpenVDMError(
            f"{cruise_dir} is named {cruise_dir.name!r}, but its cruise is "
            f"{data['cruiseID']!r}: OpenVDM names a cruise's directory after it"
        )
    # The warehouse block the API has and the file doesn't: the cruise's directory is in it
    warehouse = {
        "shipboardDataWarehouseBaseDir": str(cruise_dir.parent),
        "shipboardDataWarehouseUsername": _owner_of(cruise_dir),
        "md5SummaryFn": data.get("md5SummaryFn", ""),
        "cruiseConfigFn": path.name,
    }
    return {**data, "warehouseConfig": warehouse}


def _owner_of(path: Path) -> str:
    """The user owning path, to give the reports to; '' where that can't be told (Windows)"""
    try:
        import pwd

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (ImportError, KeyError, OSError):
        return ""


def fetch_cruise_config(site_root: str) -> dict:
    """The current cruise's configuration from OpenVDM's API"""
    url = f"{site_root}api/warehouse/getCruiseConfig"
    with reaching("OpenVDM", url), urllib.request.urlopen(url, timeout=API_TIMEOUT) as response:
        body = response.read()
    return json.loads(body)


def parse_openvdm_date(text: str | None) -> date | None:
    """OpenVDM dates are 'YYYY/MM/DD HH:MM' (or just 'YYYY/MM/DD')"""
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None


def openvdm_extent(value) -> Extent | None:
    """OpenVDM's cruiseExtent: an object with the four edges [decimal degrees], or None when
    OpenVDM has none (no track yet, or OpenVDM before 2.17)
    """
    if not value:
        return None
    try:
        return Extent(**{edge: float(value[edge]) for edge in fields_of(Extent)})
    except (KeyError, TypeError, ValueError):
        logger.warning("ignoring OpenVDM's malformed cruiseExtent: %r", value)
        return None


def utc_today() -> date:
    """Today's UTC date: fix times are UTC, so an open-ended cruise ends on this day"""
    return datetime.now(UTC).date()


def _translate(path: str, cruise_id: str) -> str:
    return path.replace("{cruiseID}", cruise_id)


@dataclass
class HookRun:
    config: QAConfig
    transfer: str
    cruise_dir: Path
    fileset_dir: Path
    output_dir: Path
    owner: str  # the warehouse user, for file ownership
    extent_source: str  # "openvdm", "site config" or "none"
    vessel_source: str  # where OpenVDM's vessel block came from: "openvdm.yaml", the cruise
    # config file's name (cruise_config.json by default), or "none"
    # What the config is missing, or had to assume
    notes: list[str] = field(default_factory=list)
    sets_extent: bool = False  # the hook sets cruiseExtent from the fixes, so has no area check


def resolve(
    cruise_config: dict,
    site: SiteConfig,
    transfer_name: str,
    vessel: OpenVDMVessel | None = None,
    set_extent: bool = False,
    feed: SiteFeed | None = None,
) -> HookRun:
    """The QA configuration and directories for one of the site's feeds (the primary by
    default) in a collection system transfer. vessel is openvdm.yaml's vessel block; without
    it, the one in the cruise's cruise_config.json is used. With set_extent, the hook will set
    OpenVDM's cruiseExtent from the primary feed's fixes, so its config has no expected area,
    unless OpenVDM is too old to have one. cruise_config is the API's, or a cruise's
    cruise_config.json as `load_cruise_json` gives it
    """
    feed = feed or site.primary
    set_extent = set_extent and feed.primary
    warehouse = cruise_config["warehouseConfig"]
    cruise_id = cruise_config["cruiseID"]
    if not cruise_id:
        raise OpenVDMError("OpenVDM has no current cruise")
    cruise_dir = Path(warehouse["shipboardDataWarehouseBaseDir"]) / cruise_id

    transfers = {t["name"]: t for t in cruise_config.get("collectionSystemTransfersConfig", [])}
    if transfer_name not in transfers:
        raise OpenVDMError(f"no collection system transfer named {transfer_name!r}")
    fileset_dir = cruise_dir / _translate(transfers[transfer_name]["destDir"], cruise_id)

    extras = {d["name"]: d for d in cruise_config.get("extraDirectoriesConfig", [])}
    output = extras.get(site.output_extra_directory)
    if output is None:
        raise OpenVDMError(
            f"no extra directory named {site.output_extra_directory!r}: add it in OpenVDM "
            "(Configuration > Extra Directories) or set [openvdm] output_extra_directory"
        )
    output_dir = cruise_dir / _translate(output["destDir"], cruise_id)

    notes = []
    which = f" for {feed.name}" if feed.name else ""
    if not feed.format:
        notes.append(
            f"the site config has no [nav] format{which}, so the QA can't run: set it "
            "(`nav-qa-processing formats` lists them)"
        )
    if not feed.files:
        notes.append(
            f"the site config has no [nav] files{which}: every file in {transfer_name} is read"
        )
    if set_extent and "cruiseExtent" not in cruise_config:
        set_extent = False
        notes.append(
            "OpenVDM has no cruiseExtent (it needs OpenVDM 2.17), so the hook can't set it"
        )
    extent, extent_source = openvdm_extent(cruise_config.get("cruiseExtent")), "openvdm"
    if set_extent:
        extent, extent_source = None, "the fixes"
        notes.append(
            "the hook sets OpenVDM's cruise extent from the fixes that pass QC, so the area "
            "check is n/a"
        )
    elif extent is None and site.extent is not None:
        extent, extent_source = site.extent, "site config"
        notes.append("OpenVDM has no cruise extent; using the site config's [extent]")
    elif extent is None:
        extent_source = "none"
        notes.append(
            "no cruise extent: OpenVDM has none (it needs OpenVDM 2.17) and the site config "
            "has no [extent], so the area check is n/a"
        )

    md5_summary = warehouse.get("md5SummaryFn")
    depart = parse_openvdm_date(cruise_config.get("cruiseStartDate"))
    arrive = parse_openvdm_date(cruise_config.get("cruiseEndDate"))
    if depart is None:
        notes.append("OpenVDM's cruise has no start date, so every file is read")
    elif arrive is None:
        # an open-ended cruise is still under way
        arrive = utc_today()
        notes.append(
            f"OpenVDM's cruise has no end date; arrive_date is today, UTC ({arrive}), so fixes "
            "after today are outside the cruise dates"
        )
    if vessel is None:
        # OpenVDM's default name (CRUISE_CONFIG_FN), when its API doesn't say
        config_filename = warehouse.get("cruiseConfigFn") or "cruise_config.json"
        vessel = cruise_vessel(cruise_dir, config_filename)
    # The site config's vessel settings win; OpenVDM's fill those it leaves blank (#112)
    ship = {}
    for name in VESSEL_SETTINGS:
        theirs, ours = vessel.settings.get(name, ""), getattr(site, name)
        ship[name] = ours or theirs
        if theirs and ours and theirs != ours:
            notes.append(
                f"{name} is {ours!r} in the site config and {theirs!r} in OpenVDM's "
                f"{vessel.source}; using the site config's"
            )
    config = QAConfig(
        cruise_id=cruise_id,
        cruise_name=cruise_config.get("cruiseName") or "",
        cruise_pi=cruise_config.get("cruisePI") or "",
        cruise_location=cruise_config.get("cruiseLocation") or "",
        vessel_name=ship["vessel_name"],
        contact_institution=ship["contact_institution"],
        contact_email=ship["contact_email"],
        extent=extent,
        depart_date=depart,
        arrive_date=arrive if depart is not None else None,
        attribution=site.attribution,
        nav_format=feed.format,
        nav_files=feed.files,
        nav_device=feed.device,
        nav_name=feed.name,
        nav_primary=feed.primary,
        manifest_path=cruise_dir / md5_summary if md5_summary else None,
        thresholds=site.thresholds,
        theme=site.theme,
    )
    return HookRun(
        config=config,
        transfer=transfer_name,
        cruise_dir=cruise_dir,
        fileset_dir=fileset_dir,
        output_dir=output_dir,
        owner=warehouse.get("shipboardDataWarehouseUsername") or "",
        extent_source=extent_source,
        vessel_source=vessel.source,
        notes=notes,
        sets_extent=set_extent,
    )


def worker_key(settings: dict) -> str:
    """openvdm.yaml's workerApiKey (OpenVDM 2.17), which setting cruiseExtent needs, or ''"""
    key = settings.get("workerApiKey")
    return str(key).strip() if key else ""


def set_cruise_extent(site_root: str, cruise_id: str, extent: Extent, key: str) -> str | None:
    """Sets OpenVDM's cruiseExtent (api/warehouse/setCruiseExtent, OpenVDM 2.17) for cruise_id,
    if it's still OpenVDM's current cruise. Returns what went wrong, or None
    """
    try:
        current = _get_json(f"{site_root}api/warehouse/getCruiseID").get("cruiseID")
    except (OSError, ValueError, AttributeError) as error:
        return f"couldn't ask OpenVDM for its current cruise: {error}"
    if current != cruise_id:
        return f"OpenVDM's current cruise is now {current!r}, not {cruise_id!r}"
    edges = {edge: round(getattr(extent, edge), EXTENT_DECIMALS) for edge in fields_of(Extent)}
    request = urllib.request.Request(
        f"{site_root}api/warehouse/setCruiseExtent",
        data=urllib.parse.urlencode({"extent": json.dumps(edges)}).encode(),
        headers={"X-Worker-Token": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT):
            pass
    except urllib.error.HTTPError as error:
        if error.code == 403:
            return (
                "OpenVDM refused the worker key (403): workerApiKey in openvdm.yaml must match "
                "WORKER_API_KEY in OpenVDM's www/app/Core/Config.php"
            )
        if error.code == 404:
            return "OpenVDM has no api/warehouse/setCruiseExtent (it needs OpenVDM 2.17)"
        detail = error.read().decode(errors="replace").strip()
        return f"OpenVDM answered {error.code}: {detail or error.reason}"
    except OSError as error:  # URLError, timeouts
        return f"couldn't reach OpenVDM ({site_root}): {error}"
    return None


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=API_TIMEOUT) as response:
        return json.loads(response.read())


def cruise_toml(run: HookRun, source: str) -> str:
    """The resolved config TOML, headed by where it came from (OpenVDM's API or a cruise's
    cruise_config.json) and what it's missing
    """
    header = [f"# Resolved from {source} for {run.transfer}"]
    header += [f"# Note: {note}" for note in run.notes]
    return "\n".join(header) + "\n" + config_to_toml(run.config)


def _glob_pattern(files: str) -> re.Pattern:
    """A regex finding paths matched by the glob `files` anywhere in a text"""
    parts = []
    i = 0
    while i < len(files):
        if files.startswith("**/", i):
            parts.append("(?:[^ ]*/)?")
            i += 3
        elif files[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif files[i] == "?":
            parts.append("[^/]")
            i += 1
        elif files[i] == "[" and "]" in files[i + 1 :]:
            end = files.index("]", i + 1)
            body = files[i + 1 : end]
            parts.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        else:
            parts.append(re.escape(files[i]))
            i += 1
    return re.compile("".join(parts))


# --min-interval's units [s]
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])")


def duration_seconds(text: str) -> float:
    """A duration such as 90s, 30m, 6h or 1d, in seconds; ValueError for anything else"""
    match = _DURATION.fullmatch(text.strip().lower())
    if match is None:
        raise ValueError(f"{text!r} isn't a duration: a number then s, m, h or d, e.g. 6h")
    return float(match.group(1)) * DURATION_UNITS[match.group(2)]


def written_within(path: Path, seconds: float, now: datetime | None = None) -> datetime | None:
    """When `path` was last written, if it exists and that's within `seconds` of `now`; else
    None. The hook's --min-interval: a long cruise's reports are rebuilt at most that often
    """
    if not path.exists():
        return None
    written = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    age = ((now or datetime.now(UTC)) - written).total_seconds()
    return written if age < seconds else None


def has_nav_changes(changed: list[str], files: str) -> bool:
    """Whether OpenVDM's {newFiles}/{updatedFiles} include nav files: paths matching the site's
    `[nav] files` glob, or any file when it has none. File names arrive space-joined (and may
    themselves contain spaces), so the glob is searched for in the text; a name with a space
    can match when it shouldn't, never the other way round
    """
    if not files:
        return any(text.strip() for text in changed)
    pattern = _glob_pattern(files)
    return any(pattern.search(text) for text in changed)


def openvdm_install_dir(openvdm_config: Path | str) -> Path:
    """OpenVDM's install directory, from its <install>/server/etc/openvdm.yaml"""
    return Path(openvdm_config).absolute().parents[2]


def queue_md5_update(install_dir: Path, run: HookRun, written: list[Path]) -> str | None:
    """Queues an MD5 summary update for the files the hook wrote, with OpenVDM's
    utils/update_md5_summary.py (OpenVDM 2.16.1). OpenVDM only hashes the files a transfer or
    data dashboard job reports, so without this the reports stay out of the summary. The files
    are given by their full paths: for the current cruise those are OpenVDM's own, and for a
    cruise from its cruise_config.json OpenVDM refuses them unless its warehouse has the cruise
    there. Returns what went wrong, or None
    """
    python = install_dir / "venv" / "bin" / "python"
    script = install_dir / "utils" / "update_md5_summary.py"
    if not (python.is_file() and script.is_file()):
        return f"no {script} with {python} (it needs OpenVDM 2.16.1 or later)"
    files = sorted(
        {
            f
            for path in written
            for f in ([path] if path.is_file() else path.rglob("*"))
            if f.is_file()
        }
    )
    if not all(f.is_relative_to(run.cruise_dir) for f in files):
        return f"the reports in {run.output_dir} are outside the cruise directory {run.cruise_dir}"
    paths = [str(f.absolute()) for f in files]
    # --cruise-id: the script's option in every OpenVDM release that has it (2.16.1-2.16.3);
    # its first version's --cruiseID was renamed before release (OpenVDM #398)
    command = [str(python), str(script), "--cruise-id", run.config.cruise_id, *paths]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=MD5_UPDATE_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"couldn't run {script}: {error}"
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip().splitlines()
        return f"{script} exited {completed.returncode}: {output[-1] if output else 'no output'}"
    return None


def chown_tree(path: Path, owner: str) -> None:
    """Give the reports to the warehouse user, as OpenVDM does, when running as root"""
    if not owner or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    for item in [path, *path.rglob("*")]:
        try:
            shutil.chown(item, user=owner, group=owner)
        except (LookupError, OSError) as error:
            logger.warning("could not chown %s to %s: %s", item, owner, error)
            return
