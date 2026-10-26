"""Runs the QA for R2R: the core QA with a cruise config, then R2R's tests; and writes what R2R
publishes, the three GeoCSV products with R2R's source values and the QA 2.0 XML, with the core
reports (figures, PDF, notebook) carrying R2R's certificate.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from nav_qa_processing import geocsv, reports
from nav_qa_processing.fileset import Fileset
from nav_qa_processing.integrations.r2r.catalog import R2R_REPOSITORY
from nav_qa_processing.integrations.r2r.certificate import NOTEBOOK_CELLS, certificate, pdf_subject
from nav_qa_processing.integrations.r2r.config import (
    CruiseConfig,
    config_to_toml,
    load_cruise_config,
)
from nav_qa_processing.integrations.r2r.rules import R2RResult, run_r2r_tests
from nav_qa_processing.integrations.r2r.xml_report import qa_xml_name, write_qa_xml
from nav_qa_processing.notebook import NotebookExtras
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
        return r2r_source(self.result)

    def __str__(self) -> str:
        return "\n".join([str(self.r2r), str(self.result)])


def r2r_source(result: NavQAResult) -> geocsv.Source:
    """The GeoCSV source values, with R2R as the repository"""
    return replace(result.source, repository=R2R_REPOSITORY)


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


PACKAGE = "nav_qa_processing.integrations.r2r"


def notebook_extras(run: R2RRun) -> NotebookExtras:
    """The notebook re-runs R2R's tests and writes the reports as write_r2r_reports does"""
    config = run.result.config
    return NotebookExtras(
        heading=f" fileset {config.fileset_id}" if config.fileset_id else "",
        reports=" and R2R QA 2.0 XML",
        checks=", then R2R's six tests",
        imports=(
            f"from {PACKAGE}.certificate import bound_text, certificate, pdf_subject, "
            "result_text\n"
            f"from {PACKAGE}.config import load_cruise_config, loads_cruise_config\n"
            f"from {PACKAGE}.reports import r2r_source\n"
            f"from {PACKAGE}.rules import run_r2r_tests\n"
            f"from {PACKAGE}.xml_report import qa_xml_name, write_qa_xml\n"
        ),
        load_config="load_cruise_config",
        config_toml=config_to_toml(config),
        loads_config="loads_cruise_config",
        after_run="\nr2r = run_r2r_tests(result.stats)\nprint(r2r)",
        cells=list(NOTEBOOK_CELLS),
        report_arguments=(
            ",\n    source=r2r_source(result),\n"
            "    pdf_front=certificate(result, r2r),\n"
            "    pdf_subject=pdf_subject(r2r),"
        ),
        write=(
            "print(write_qa_xml(result, r2r, OUTPUT_DIR / qa_xml_name(result.config, "
            "result.finished)))\n"
        ),
    )


def write_r2r_reports(
    run: R2RRun,
    output: Path,
    theme: Theme | None = None,
    theme_file: Path | None = None,
    execute_notebook: bool = False,
    r2rnav: bool = False,
) -> list[Path]:
    """The core reports with R2R's source values and certificate, then the QA 2.0 XML, and with
    `r2rnav` the products as legacy r2rnav too (#146)
    """
    written = reports.write_reports(
        run.result,
        output,
        run.source,
        theme,
        pdf_front=certificate(run.result, run.r2r),
        pdf_subject=pdf_subject(run.r2r),
        theme_file=theme_file,
        execute_notebook=execute_notebook,
        notebook_extras=notebook_extras(run),
    )
    written.append(write_qa_xml(run.result, run.r2r, report_paths(run, output)["xml"]))
    if r2rnav:
        from nav_qa_processing.integrations.r2r import r2rnav as legacy

        written += legacy.write_products(run.result, output)
    return written
