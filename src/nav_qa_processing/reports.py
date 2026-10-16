"""The core reports of a QA run: the three GeoCSV products,
`<CRUISE>_bestres|1min|control.geoCSV`, and every figure as an SVG in `<CRUISE>_plots/`. The
PDF and notebook join them in Phase 4 (#38, #39).
"""

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from nav_qa_processing import geocsv, plots
from nav_qa_processing.qa import NavQAResult
from nav_qa_processing.theme import DEFAULT_THEME, Theme


def report_paths(output: Path, cruise_id: str) -> dict[str, Path]:
    """What write_reports writes: the products by name, and "plots", the figures' directory"""
    paths = {
        name: Path(output) / geocsv.file_name(cruise_id, product)
        for name, product in geocsv.PRODUCTS.items()
    }
    paths["plots"] = Path(output) / f"{cruise_id}_plots"
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
) -> list[Path]:
    """The three GeoCSV products, created at the run's end, and the figures as SVGs. `source`
    replaces the config's GeoCSV source values (e.g. R2R's); `theme` is the reports' look
    (`theme.resolve_theme`)
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
    plots_dir = report_paths(output, result.config.cruise_id)["plots"]
    written += write_figures(result, plots_dir, theme).values()
    return written
