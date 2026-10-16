"""Runs the QA for R2R: the core QA with a cruise config, then R2R's tests; and writes what R2R
publishes: the core reports (the three GeoCSV products) with R2R's source values, and the QA
2.0 XML.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from nav_qa_processing import geocsv, reports
from nav_qa_processing.fileset import Fileset
from nav_qa_processing.integrations.r2r.catalog import R2R_REPOSITORY
from nav_qa_processing.integrations.r2r.config import CruiseConfig, load_cruise_config
from nav_qa_processing.integrations.r2r.rules import R2RResult, run_r2r_tests
from nav_qa_processing.integrations.r2r.xml_report import qa_xml_name, write_qa_xml
from nav_qa_processing.qa import NavQAResult, run_qa
from nav_qa_processing.readable import Readable
from nav_qa_processing.theme import Theme


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
    config = run.result.config
    return {
        **reports.report_paths(output, config.cruise_id),
        "xml": Path(output) / qa_xml_name(config, run.result.finished),
    }


def write_r2r_reports(
    run: R2RRun,
    output: Path,
    theme: Theme | None = None,
    theme_file: Path | None = None,
    execute_notebook: bool = False,
) -> list[Path]:
    """The core reports with R2R's source values, then the QA 2.0 XML"""
    written = reports.write_reports(
        run.result,
        output,
        run.source,
        theme,
        theme_file=theme_file,
        execute_notebook=execute_notebook,
    )
    written.append(write_qa_xml(run.result, run.r2r, report_paths(run, output)["xml"]))
    return written
