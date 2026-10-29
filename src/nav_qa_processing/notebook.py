"""Writes a Jupyter notebook that re-runs a fileset's QA step by step: the config, `run_qa`,
the checks, statistics and QC flags, every figure, the products, and writing the reports.

The cells are `notebook_template.py`, a Python file Ruff checks, with markers this module fills
for the run (#144); `tests/test_notebook.py` executes the result. Integrations add their imports
and cells through `NotebookExtras` (R2R's tests and XML), never by core knowing about them.
"""

import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from nav_qa_processing.config import config_to_toml, load_config
from nav_qa_processing.qa import NavQAResult

# The notebook's cells, as Python (#144); see its docstring for the markers
TEMPLATE = files("nav_qa_processing") / "notebook_template.py"
MARKER = re.compile(r"^.*# @(?P<name>\w+)$")


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
    # The feed this run assessed, when the config names its feeds ([[nav]])
    nav = None if extras.load_config or not config.nav_name else config.nav_name
    feed = f", nav={nav!r}" if nav else ""
    lines = f"config = {extras.load_config or 'load_config'}(CONFIG{feed})\n"
    imports = "" if extras.load_config else "from nav_qa_processing.config import load_config\n"
    if load_config(path, nav=nav).nav_format != config.nav_format:  # run -f overrode it
        lines += f"config = replace(config, nav_format={config.nav_format!r})\n"
        imports += "from dataclasses import replace\n"
    return f"CONFIG = Path({str(path)!r})\n", lines, imports


def _template() -> list[tuple[str, list[str]]]:
    """notebook_template.py's cells, (kind, lines), in order"""
    cells: list[tuple[str, list[str]]] = []
    for line in TEMPLATE.read_text().splitlines():
        if line.startswith("# %%"):
            cells.append(("markdown" if line == "# %% [markdown]" else "code", []))
        elif cells:
            cells[-1][1].append(line)
    return cells


def _code(lines: list[str], code: dict[str, str]) -> str:
    """A template code cell with its markers filled"""
    out: list[str] = []
    for line in lines:
        if line in ("# fmt: off", "# fmt: on"):
            continue
        if line.startswith("# @raw "):
            out.append(line.removeprefix("# @raw "))
        elif marker := MARKER.match(line):
            if replacement := code[marker["name"]].strip("\n"):
                out.append(replacement)
        elif line or (out and out[-1]):  # an empty marker leaves no second blank line
            out.append(line)
    return "\n".join(out).strip("\n")


def _text(lines: list[str], values: dict[str, str]) -> str:
    """A template markdown cell with its `{name}`s filled"""
    text = "\n".join(line.removeprefix("#").removeprefix(" ") for line in lines).strip("\n")
    for name, value in values.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def build_notebook(
    result: NavQAResult,
    output_dir: Path,
    extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
    products: bool = True,
) -> nbformat.NotebookNode:
    """The notebook in notebook_template.py, for this run (theme_file is the --theme file the
    reports were written with, if any; without `products`, it writes the reports without the
    GeoCSV products, as they were)
    """
    config = result.config
    extras = extras or NotebookExtras()
    config_line, config_lines, config_imports = _config_code(result, extras)
    values = {
        "cruise_id": config.cruise_id,
        "heading": extras.heading,
        "cruise_name": config.cruise_name,
        "reports": extras.reports,
        "nav_format": config.nav_format,
        "checks": extras.checks,
        "versions": ", ".join(f"{name} {version}" for name, version in result.versions.items()),
    }
    code = {
        "config_path": config_line,
        "fileset": f"FILESET = Path({str(Path(result.fileset.root).resolve())!r})",
        "theme_file": (
            f"THEME_FILE = Path({str(Path(theme_file).resolve())!r})"
            if theme_file
            else "THEME_FILE = None  # a --theme file; the config's [theme] applies on top"
        ),
        "output_dir": f"OUTPUT_DIR = Path({str(Path(output_dir).resolve())!r})",
        "config_imports": config_imports,
        "extras_imports": extras.imports,
        "config_lines": config_lines,
        "after_run": extras.after_run,
        "report_arguments": "    result, OUTPUT_DIR, theme_file=THEME_FILE, notebook=False"
        + ("" if products else ", products=False")
        + extras.report_arguments,
        "write": extras.write,
    }
    cells = []
    for kind, lines in _template():
        if kind == "markdown":
            cells.append(new_markdown_cell(_text(lines, values)))
        elif [line for line in lines if line.strip()] == ["# @cells"]:
            cells += extras.cells
        else:
            cells.append(new_code_cell(_code(lines, code)))
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
    products: bool = True,
) -> Path:
    """Writes the notebook; with `execute`, runs it first and saves its outputs (needs nbclient
    and ipykernel, in the dev group). The last cell, which writes the reports, isn't run: they
    were just written, and running it would rewrite them (and an integration's timestamped
    reports, such as R2R's XML, again under a new name)
    """
    path = Path(path)
    notebook = build_notebook(result, Path(output_dir), extras, theme_file, products)
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
