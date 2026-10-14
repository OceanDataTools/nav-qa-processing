"""Runs the QA for R2R: the core QA with a cruise config, then R2R's tests; and writes what R2R
publishes: the three GeoCSV products with R2R's source values, and the QA 2.0 XML.

The PDF, figures and notebook join these when core has them (Phase 4).
"""

from dataclasses import dataclass, replace
from pathlib import Path

from nav_qa_processing import geocsv
from nav_qa_processing.fileset import Fileset
from nav_qa_processing.integrations.r2r.catalog import R2R_REPOSITORY
from nav_qa_processing.integrations.r2r.config import CruiseConfig, load_cruise_config
from nav_qa_processing.integrations.r2r.rules import R2RResult, run_r2r_tests
from nav_qa_processing.integrations.r2r.xml_report import qa_xml_name, write_qa_xml
from nav_qa_processing.qa import NavQAResult, run_qa
from nav_qa_processing.readable import Readable


@dataclass
class R2RRun(Readable):
    result: NavQAResult  # its config is the CruiseConfig
    r2r: R2RResult
    config_path: Path | None = None

    @property
    def source(self) -> geocsv.Source:
        """The GeoCSV source values, with R2R as the repository"""
        return replace(self.result.source, repository=R2R_REPOSITORY)

    def __str__(self) -> str:
        return "\n".join([str(self.r2r), str(self.result)])


def run_r2r(fileset: Fileset | Path | str, config: CruiseConfig | Path | str) -> R2RRun:
    config_path = None if isinstance(config, CruiseConfig) else Path(config)
    if config_path is not None:
        config = load_cruise_config(config_path)
    result = run_qa(fileset, config)
    result.config_path = config_path
    return R2RRun(result, run_r2r_tests(result.stats), config_path)


def report_paths(run: R2RRun, output: Path) -> dict[str, Path]:
    """What write_r2r_reports writes, by R2R's names"""
    cruise_id = run.result.config.cruise_id
    paths = {
        name: output / geocsv.file_name(cruise_id, product)
        for name, product in geocsv.PRODUCTS.items()
    }
    paths["xml"] = output / qa_xml_name(run.result.config, run.result.finished)
    return paths


def write_r2r_reports(run: R2RRun, output: Path) -> list[Path]:
    """The three GeoCSV products and the QA 2.0 XML, into output"""
    output.mkdir(parents=True, exist_ok=True)
    result = run.result
    frames = {
        geocsv.BESTRES.name: result.bestres,
        geocsv.ONE_MINUTE.name: result.one_minute,
        geocsv.CONTROL.name: result.control,
    }
    written = [
        geocsv.write(
            output,
            geocsv.PRODUCTS[name],
            frame,
            result.nav.decimals,
            run.source,
            created=result.finished,
        )
        for name, frame in frames.items()
    ]
    written.append(write_qa_xml(result, run.r2r, report_paths(run, output)["xml"]))
    return written
