"""The core reports of a QA run: the three GeoCSV products,
`<CRUISE>_bestres|1min|control.geoCSV`, every figure as an SVG in `<CRUISE>_plots/`, and the
PDF report, `<CRUISE>_qa_report.pdf`, which embeds them. The notebook joins them in #39.
"""

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from nav_qa_processing import geocsv, plots
from nav_qa_processing.pdf_report import FrontPages, write_pdf
from nav_qa_processing.qa import NavQAResult
from nav_qa_processing.theme import DEFAULT_THEME, Theme


def report_paths(output: Path, cruise_id: str) -> dict[str, Path]:
    """What write_reports writes: the products by name, and "plots", the figures' directory"""
    paths = {
        name: Path(output) / geocsv.file_name(cruise_id, product)
        for name, product in geocsv.PRODUCTS.items()
    }
    paths["plots"] = Path(output) / f"{cruise_id}_plots"
    paths["pdf"] = Path(output) / f"{cruise_id}_qa_report.pdf"
    return paths


def save_svg(figure: Figure, path: Path) -> Path:
    """The figure as SVG, then closed. No date in the metadata, and a fixed salt for
    matplotlib's clip path IDs (random by default), so re-running gives identical files
    """
    with matplotlib.rc_context({"svg.hashsalt": "nav-qa-processing"}):
        figure.savefig(path, format="svg", metadata={"Date": None})
    plt.close(figure)
    return path


def write_figures(
    result: NavQAResult, directory: Path, theme: Theme = DEFAULT_THEME
) -> dict[str, Path]:
    """Every figure in `plots.FIGURES` as `<name>.svg` in directory"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return {
        name: save_svg(make(result, theme), directory / f"{name}.svg")
        for name, make in plots.FIGURES.items()
    }


def write_reports(
    result: NavQAResult,
    output: Path,
    source: geocsv.Source | None = None,
    theme: Theme = DEFAULT_THEME,
    pdf_front: FrontPages | None = None,
    pdf_subject: str = "Navigation quality assessment",
) -> list[Path]:
    """The three GeoCSV products, created at the run's end, the figures as SVGs and the PDF.
    `source` replaces the config's GeoCSV source values (e.g. R2R's); `theme` is the reports'
    look (`theme.resolve_theme`); `pdf_front` makes the pages an integration puts first
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
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
            source or result.source,
            created=result.finished,
        )
        for name, frame in frames.items()
    ]
    paths = report_paths(output, result.config.cruise_id)
    svgs = write_figures(result, paths["plots"], theme)
    written += svgs.values()
    written.append(write_pdf(result, paths["pdf"], svgs, pdf_front, pdf_subject, theme))
    return written
