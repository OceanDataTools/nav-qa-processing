"""Writes a Jupyter notebook that re-runs a fileset's QA step by step: the config, `run_qa`,
the checks, statistics and QC flags, every figure, the products, and writing the reports.

Import lines and calls are written as text, so renaming a public function or module changes
the notebook; `tests/test_notebook.py` executes it. Integrations add their imports and cells
through `NotebookExtras` (R2R's tests and XML), never by core knowing about them.
"""

from dataclasses import dataclass, field
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from nav_qa_processing.config import config_to_toml, load_config
from nav_qa_processing.qa import NavQAResult


@dataclass
class NotebookExtras:
    """What an integration adds to the core notebook"""

    heading: str = ""  # after the title, e.g. " fileset 169851"
    reports: str = ""  # what else the reports are, e.g. " and R2R's QA 2.0 XML"
    checks: str = ""  # what else is run, e.g. ", then R2R's six tests"
    imports: str = ""  # import lines for the setup cell
    load_config: str = ""  # the function that loads CONFIG, e.g. load_cruise_config
    # Without a config file: the config as the integration's TOML, and what reads it
    config_toml: str = ""
    loads_config: str = ""  # e.g. loads_cruise_config
    after_run: str = ""  # setup cell code after run_qa
    cells: list = field(default_factory=list)  # after the setup cell
    write: str = ""  # report-writing code after the core reports'
    report_arguments: str = ""  # more write_reports arguments, e.g. ", source=..."


def _config_code(result: NavQAResult, extras: NotebookExtras) -> tuple[str, str, str]:
    """The paths cell's CONFIG line, the setup cell's config lines, and the imports they need.
    Without a config file (e.g. `run -f nav29`, or a config built in Python), the config's own
    TOML is written into the notebook
    """
    config = result.config
    if result.config_path is None:
        toml = extras.config_toml or config_to_toml(config)
        text = "".join(f"    {line!r}\n" for line in toml.splitlines(keepends=True))
        return (
            f"# The run had no config file: its config as TOML\nCONFIG = (\n{text})\n",
            f"config = {extras.loads_config or 'loads_config'}(CONFIG)\n",
            "" if extras.loads_config else "from nav_qa_processing.config import loads_config\n",
        )
    path = Path(result.config_path).resolve()
    lines = f"config = {extras.load_config or 'load_config'}(CONFIG)\n"
    imports = "" if extras.load_config else "from nav_qa_processing.config import load_config\n"
    if load_config(path).nav_format != config.nav_format:  # run -f overrode the config's
        lines += f"config = replace(config, nav_format={config.nav_format!r})\n"
        imports += "from dataclasses import replace\n"
    return f"CONFIG = Path({str(path)!r})\n", lines, imports


def build_notebook(
    result: NavQAResult,
    output_dir: Path,
    extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
) -> nbformat.NotebookNode:
    """theme_file is the --theme file the reports were written with, if any"""
    config = result.config
    extras = extras or NotebookExtras()
    config_line, config_lines, config_imports = _config_code(result, extras)
    versions = ", ".join(f"{name} {version}" for name, version in result.versions.items())
    cells = [
        new_markdown_cell(
            f"# Navigation QA: {config.cruise_id}{extras.heading}\n\n"
            f"{config.cruise_name}\n\n"
            "Re-runs the quality assessment behind this fileset's products and PDF report"
            + extras.reports
            + f": it reads the primary GNSS feed ({config.nav_format}), flags bad fixes, makes "
            "bestres, 1min and control, and computes the QA statistics and checks"
            + extras.checks
            + ". Edit the paths or the config's `[thresholds]` and re-run to change the "
            f"assessment.\n\nGenerated with {versions}."
        ),
        new_code_cell(
            "from pathlib import Path\n\n"
            "# Inputs and outputs\n"
            + config_line
            + f"FILESET = Path({str(Path(result.fileset.root).resolve())!r})\n"
            + (
                f"THEME_FILE = Path({str(Path(theme_file).resolve())!r})\n"
                if theme_file
                else "THEME_FILE = None  # a --theme file; the config's [theme] applies on top\n"
            )
            + f"OUTPUT_DIR = Path({str(Path(output_dir).resolve())!r})"
        ),
        new_code_cell(
            "%matplotlib inline\n"
            "import matplotlib.pyplot as plt\n"
            "import pandas as pd\n"
            "from IPython.display import display\n\n"
            "from nav_qa_processing import plots, reports, stats\n"
            "from nav_qa_processing import quantities as q\n"
            "from nav_qa_processing.qa import run_qa\n"
            "from nav_qa_processing.qc import DESCRIPTIONS, Flag\n"
            "from nav_qa_processing.theme import resolve_theme\n"
            + config_imports
            + extras.imports
            + '\npd.set_option("display.max_colwidth", 120)\n\n'
            + config_lines
            + "result = run_qa(FILESET, config)\n"
            "theme = resolve_theme(THEME_FILE, result.config)\n"
            "print(result)" + extras.after_run
        ),
        *extras.cells,
        new_markdown_cell(
            "## Checks\n\n"
            "Completeness, gaps, records out of sequence, bad quality indicators, unreasonable "
            "speeds and accelerations, and the expected area, against the config's "
            "`[thresholds]`."
        ),
        new_code_cell("display(pd.DataFrame([vars(c) for c in result.stats.checks]))"),
        new_markdown_cell(
            "### Checks by file\n\n"
            "What each check counted, per nav file. A speed, a step out of sequence or a gap is "
            "counted in the file of the fix that ends it."
        ),
        new_code_cell("display(stats.counts_by_file(result.nav, result.stats))"),
        new_markdown_cell("## Statistics"),
        new_code_cell(
            "s = result.stats\n"
            "ranges = {\n"
            "    q.NSV: (s.nsv, None),\n"
            '    q.HDOP: (s.hdop, result.nav.decimals.get("hdop")),\n'
            '    q.ANTENNA_HEIGHT: (s.altitude, result.nav.decimals.get("alt")),\n'
            "    q.SPEED_MADE_GOOD: (s.speed, None),\n"
            "    q.HORIZONTAL_ACCELERATION: (s.acceleration, None),\n"
            "}\n"
            "display(pd.DataFrame(\n"
            '    [{"quantity": quantity.label, "minimum": quantity.format(r.minimum, places),\n'
            '      "maximum": quantity.format(r.maximum, places)}\n'
            "     for quantity, (r, places) in ranges.items()],\n"
            "))\n"
            "print(q.FIXES.label, q.FIXES.format(s.records))\n"
            "print(q.MODAL_INTERVAL.label, q.MODAL_INTERVAL.format(s.modal_interval))\n"
            "print(q.LONGEST_GAP.label, q.LONGEST_GAP.format(s.longest_gap))"
        ),
        new_markdown_cell(
            "## Quality control\n\n"
            "Records flagged by each flag (a record can have more than one), QC's notes, and "
            "the reader's and manifest's errors."
        ),
        new_code_cell(
            "display(pd.DataFrame(\n"
            '    [{"flag": f.name, "meaning": DESCRIPTIONS[f], "records": result.qc.count(f)}\n'
            "     for f in Flag if result.qc.count(f)],\n"
            "))\n"
            "for note in result.qc.notes:\n"
            "    print(note)\n"
            "for error in result.errors:\n"
            '    print("error:", error)'
        ),
        new_markdown_cell("## Figures"),
        new_code_cell(
            "for name, make in plots.FIGURES.items():\n"
            "    figure = make(result, theme)\n"
            "    display(figure)\n"
            "    plt.close(figure)"
        ),
        new_markdown_cell(
            "## Products\n\n"
            "bestres is every record, with its QC flags (0 for a good fix) and speed and course "
            "over ground; 1min is the good fix nearest each whole minute; control is "
            "Douglas–Peucker on 1min."
        ),
        new_code_cell(
            "display(result.bestres.head())\n"
            "display(result.one_minute.head())\n"
            "display(result.control)"
        ),
        new_markdown_cell("## Write the reports"),
        new_code_cell(
            "written = reports.write_reports(\n"
            "    result, OUTPUT_DIR, theme_file=THEME_FILE, notebook=False"
            + extras.report_arguments
            + "\n)\n"
            "for path in written:\n"
            "    print(path)\n" + extras.write
        ),
    ]
    notebook = new_notebook(cells=cells)
    notebook.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    notebook.metadata["language_info"] = {"name": "python"}
    return notebook


def write_notebook(
    result: NavQAResult,
    path: Path | str,
    output_dir: Path | str,
    execute: bool = False,
    extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
) -> Path:
    """Writes the notebook; with `execute`, runs it first and saves its outputs (needs nbclient
    and ipykernel, in the dev group). The last cell, which writes the reports, isn't run: they
    were just written, and running it would rewrite them (and an integration's timestamped
    reports, such as R2R's XML, again under a new name)
    """
    path = Path(path)
    notebook = build_notebook(result, Path(output_dir), extras, theme_file)
    if execute:
        try:
            from nbclient import NotebookClient
        except ImportError as error:
            raise RuntimeError("executing the notebook needs nbclient and ipykernel") from error
        write = notebook.cells.pop()
        NotebookClient(
            notebook, timeout=1800, resources={"metadata": {"path": str(path.parent)}}
        ).execute()
        notebook.cells.append(write)
    nbformat.write(notebook, path)
    return path
