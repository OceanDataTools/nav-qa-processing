"""The core reports of a QA run: the three GeoCSV products,
`<CRUISE>_bestres|1min|control.geoCSV`, every figure as an SVG in `<CRUISE>_plots/`, the PDF
report, `<CRUISE>_qa_report.pdf`, which embeds them, and `<CRUISE>_qa.ipynb`, a notebook that
re-runs the assessment.
"""

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from nav_qa_processing import geocsv, plots
from nav_qa_processing.filenames import report_stem
from nav_qa_processing.notebook import NotebookExtras, write_notebook
from nav_qa_processing.pdf_report import FrontPages, write_pdf
from nav_qa_processing.qa import NavQAResult
from nav_qa_processing.theme import DEFAULT_THEME, Theme, resolve_theme


def report_paths(output: Path, stem: str, products: bool = True) -> dict[str, Path]:
    """What write_reports writes for a config's `stem` (the cruise ID, and a non-primary feed's
    name): the products by name (unless not `products`), and "plots", the figures' directory.
    Each is a valid file name in output whatever the stem (`filenames.report_stem`)
    """
    paths = {
        name: Path(output) / geocsv.file_name(stem, product)
        for name, product in geocsv.PRODUCTS.items()
        if products
    }
    stem = report_stem(stem)
    paths["plots"] = Path(output) / f"{stem}_plots"
    paths["pdf"] = Path(output) / f"{stem}_qa_report.pdf"
    paths["notebook"] = Path(output) / f"{stem}_qa.ipynb"
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
    theme: Theme | None = None,
    pdf_front: FrontPages | None = None,
    pdf_subject: str = "Navigation quality assessment",
    theme_file: Path | None = None,
    notebook: bool = True,
    execute_notebook: bool = False,
    notebook_extras: NotebookExtras | None = None,
    products: bool = True,
) -> list[Path]:
    """The three GeoCSV products, created at the run's end, the figures as SVGs, the PDF and
    the notebook. `source` replaces the config's GeoCSV source values (e.g. R2R's); the look is
    `theme`, or the `theme_file` (a --theme file) with the config's [theme] on top; `pdf_front`
    and `notebook_extras` are what an integration adds. The notebook itself writes the reports
    with `notebook=False`, so it doesn't replace itself. Without `products`, the GeoCSV products
    aren't written (`--no-products`); the PDF still gives their record counts
    """
    if theme is None:
        theme = resolve_theme(theme_file, result.config)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    frames = {
        geocsv.BESTRES.name: result.bestres,
        geocsv.ONE_MINUTE.name: result.one_minute,
        geocsv.CONTROL.name: result.control,
    }
    written: list[Path] = []
    if products:
        written += [
            geocsv.write(
                output,
                geocsv.PRODUCTS[name],
                frame,
                result.nav.decimals,
                source or result.source,
                created=result.finished,
                stem=result.config.stem,
            )
            for name, frame in frames.items()
        ]
    paths = report_paths(output, result.config.stem, products)
    svgs = write_figures(result, paths["plots"], theme)
    written += svgs.values()
    written.append(
        write_pdf(result, paths["pdf"], svgs, pdf_front, pdf_subject, theme, products=products)
    )
    if notebook:
        written.append(
            write_notebook(
                result,
                paths["notebook"],
                output,
                execute=execute_notebook,
                extras=notebook_extras,
                theme_file=theme_file,
                products=products,
            )
        )
    return written
