"""Runs the core QA on a fileset: reads the primary GNSS feed, flags bad fixes, makes the three
products, and computes the QA statistics and core checks. `run_qa(fileset, config=None)` is the
core pipeline; every writer consumes the `NavQAResult` it returns.
"""

import importlib.metadata
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from nav_qa_processing import __version__, formats, geocsv, products, qc, rawnav, stats
from nav_qa_processing.config import Extent, QAConfig, load_config
from nav_qa_processing.fileset import Fileset, load_fileset, verify_manifest
from nav_qa_processing.readable import Readable, plural, timestamp

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """The config can't run the QA (e.g. no nav format); the message says what to set"""


@dataclass
class NavQAResult(Readable):
    config: QAConfig
    fileset: Fileset
    nav: rawnav.RawNav
    qc: qc.QCResult
    bestres: pd.DataFrame
    one_minute: pd.DataFrame
    control: pd.DataFrame
    stats: stats.NavStats
    errors: list[str]  # the fileset's and the reader's, as text
    started: datetime
    finished: datetime
    versions: dict[str, str] = field(default_factory=dict)
    config_path: Path | None = None  # set when the config was loaded from a file

    @property
    def source(self) -> geocsv.Source:
        """The GeoCSV header's source values, from the config"""
        return geocsv.Source(
            cruise_id=self.config.cruise_id,
            cruise_doi=self.config.cruise_doi,
            fileset_doi=self.config.fileset_doi,
            attribution=self.config.attribution,
        )

    @property
    def track_extent(self) -> Extent | None:
        """The box around the fixes that passed QC, or None when none did"""
        good = self.bestres[self.bestres["flags"] == 0]
        return Extent.around(good["lon"], good["lat"])

    def __str__(self) -> str:
        seconds = (self.finished - self.started).total_seconds()
        versions = ", ".join(f"{name} {version}" for name, version in self.versions.items())
        flagged = int((self.qc.flags != 0).sum())
        by_flag = ", ".join(
            f"{f.name.lower()} {self.qc.count(f)}" for f in qc.Flag if self.qc.count(f)
        )
        lines = [
            (
                f"{self.config.identifier}: {plural(len(self.nav.frame), 'fix', 'fixes')} "
                f"({self.config.nav_format}) from {plural(len(self.nav.files), 'file')} "
                f"in {self.fileset.root}"
            ),
            f"  run {timestamp(self.started)}, {seconds:.1f} s"
            + (f"; {versions}" if versions else ""),
            f"  flagged: {flagged}" + (f" ({by_flag})" if by_flag else ""),
            *(f"    {note}" for note in self.qc.notes),
            (
                f"  products: bestres {len(self.bestres)}, 1min {len(self.one_minute)}, "
                f"control {len(self.control)}"
            ),
            *(f"  error: {error}" for error in self.errors),
            *(f"  {line}" for line in str(self.stats).splitlines()),
        ]
        return "\n".join(lines)


def _versions() -> dict[str, str]:
    versions = {"nav-qa-processing": __version__}
    for package in ("numpy", "pandas", "pyproj", "shapely"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unknown"
    return versions


def resolve_config(config: QAConfig | Path | str | None, fileset_dir: Path | str) -> QAConfig:
    """The config for a run: as given, loaded from a TOML, or empty, titled after the fileset
    directory when it has no cruise ID
    """
    if config is None:
        config = QAConfig()
    elif not isinstance(config, QAConfig):
        config = load_config(config)
    if not config.cruise_id:
        config = replace(config, cruise_id=Path(fileset_dir).resolve().name)
    return config


def cruise_window(config: QAConfig) -> tuple[datetime | None, datetime | None]:
    """The cruise's [start, end) in UTC, from its depart and arrive dates, if given"""
    start = datetime.combine(config.depart_date, time(), UTC) if config.depart_date else None
    end = (
        datetime.combine(config.arrive_date, time(), UTC) + timedelta(days=1)
        if config.arrive_date
        else None
    )
    return start, end


def run_qa(
    fileset: Fileset | Path | str, config: QAConfig | Path | str | None = None
) -> NavQAResult:
    started = datetime.now(UTC)
    config_path = None if config is None or isinstance(config, QAConfig) else Path(config)
    root = fileset.root if isinstance(fileset, Fileset) else fileset
    config = resolve_config(config, root)
    if not config.nav_format:
        readable = ", ".join(sorted(formats.READERS))
        raise ConfigError(
            f"no nav format: set [nav] format in the config (formats that can be read: {readable})"
        )
    formats.get(config.nav_format)  # unknown or unsupported: ValueError naming the readable ones
    if not isinstance(fileset, Fileset):
        fileset = load_fileset(fileset, files=config.nav_files, manifest_path=config.manifest_path)
    logger.info("%s: %s", config.identifier, plural(len(fileset.files), "nav file"))
    errors = verify_manifest(fileset)

    start, end = cruise_window(config)
    window = {k: v for k, v in (("start", start), ("end", end)) if v is not None}
    nav = rawnav.read_fileset(fileset.files, config.nav_format, **window)
    errors += nav.errors
    logger.info(
        "read %s (%s) from %s",
        plural(len(nav.frame), "fix", "fixes"),
        config.nav_format,
        plural(len(nav.files), "file"),
    )
    flags = qc.flag(nav, config.thresholds, start, end)
    logger.info("QC flagged %d", int((flags.flags != 0).sum()))
    best = products.bestres(nav, flags)
    one_minute = products.sample(best)
    return NavQAResult(
        config=config,
        fileset=fileset,
        nav=nav,
        qc=flags,
        bestres=best,
        one_minute=one_minute,
        control=products.control(one_minute),
        stats=stats.compute(nav, flags, config.thresholds, config.extent),
        errors=errors,
        started=started,
        finished=datetime.now(UTC),
        versions=_versions(),
        config_path=config_path,
    )
