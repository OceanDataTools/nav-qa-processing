"""The notebook that re-runs the assessment (#39), on the synthetic cruise of test_plots."""

from dataclasses import replace

import nbformat
import pytest
from nbformat.v4 import new_markdown_cell
from test_plots import cruise, fileset

from nav_qa_processing import maps, reports
from nav_qa_processing.config import Extent
from nav_qa_processing.notebook import NotebookExtras, build_notebook
from nav_qa_processing.qa import run_qa


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def source(notebook) -> str:
    return "\n".join(cell.source for cell in notebook.cells)


def outputs(cell, kind="stream") -> str:
    """A cell's printed output: stdout, so a warning on stderr can't take its place (#110)"""
    return "".join(
        o.get("text", "")
        for o in cell.outputs
        if o.output_type == kind and o.get("name", "stdout") == "stdout"
    )


def errors(notebook) -> list:
    return [
        o
        for c in notebook.cells
        if c.cell_type == "code"
        for o in c.outputs
        if o.output_type == "error"
    ]


def test_the_notebook_reruns_the_assessment(tmp_path):
    """A config built in Python goes into the notebook as TOML, extent and all"""
    result = cruise(tmp_path / "in", extent=Extent(-117.4, -117.2, 32.5, 32.8))
    reports.write_reports(result, tmp_path / "out", execute_notebook=True)
    notebook = nbformat.read(tmp_path / "out" / "TEST_qa.ipynb", as_version=4)
    assert not errors(notebook), errors(notebook)
    assert "config = loads_config(CONFIG)" in source(notebook)
    code = [c for c in notebook.cells if c.cell_type == "code"]
    setup = outputs(code[1])
    assert setup.splitlines()[0] == str(result).splitlines()[0]  # the same fixes and files
    checks = "".join(
        o["data"].get("text/plain", "") for o in code[2].outputs if o.output_type == "display_data"
    )
    expected = next(c for c in result.stats.checks if c.name == "Within the expected area")
    assert expected.status == "pass" and "Within the expected area" in checks  # the extent
    assert "no extent configured" not in checks
    assert "write_reports(" in code[-1].source and not code[-1].outputs  # left for the reader


def test_the_last_cell_rewrites_the_reports_but_not_the_notebook(tmp_path):
    from nbclient import NotebookClient

    result = cruise(tmp_path / "in")
    notebook = build_notebook(result, tmp_path / "out")
    NotebookClient(notebook, timeout=600).execute()
    assert not errors(notebook), errors(notebook)
    written = outputs(notebook.cells[-1]).splitlines()
    assert any(line.endswith("TEST_qa_report.pdf") for line in written)
    assert not any(line.endswith(".ipynb") for line in written)


def test_a_config_file_is_loaded_and_a_format_override_kept(tmp_path):
    path = tmp_path / "cruise.toml"
    path.write_text('[cruise]\nid = "TEST"\n\n[nav]\nformat = "nav29"\n')
    directory = fileset(tmp_path / "in", [(s, -117.3, 32.6 + s * 1e-5, 2) for s in range(30)])
    result = run_qa(directory, path)
    text = source(build_notebook(result, tmp_path / "out"))
    assert f"CONFIG = Path({str(path.resolve())!r})" in text
    assert "config = load_config(CONFIG)" in text and "replace(" not in text
    overridden = replace(result, config=replace(result.config, nav_format="nav01"))
    assert "config = replace(config, nav_format='nav01')" in source(
        build_notebook(overridden, tmp_path / "out")
    )


def test_integrations_add_imports_cells_and_writes(tmp_path):
    result = cruise(tmp_path / "in")
    extras = NotebookExtras(
        heading=" fileset 1",
        imports="import json\n",
        after_run="\nprint('extra')",
        cells=[new_markdown_cell("## An integration's section")],
        write="print('also written')\n",
        report_arguments=", pdf_subject='x'",
    )
    notebook = build_notebook(result, tmp_path / "out", extras)
    text = source(notebook)
    assert notebook.cells[0].source.startswith("# Navigation QA: TEST fileset 1")
    assert (
        "import json" in notebook.cells[2].source and "print('extra')" in notebook.cells[2].source
    )
    assert notebook.cells[3].source == "## An integration's section"
    assert text.index("pdf_subject='x'") < text.index("print('also written')")


def test_the_theme_file_is_kept(tmp_path):
    result = cruise(tmp_path / "in")
    theme = tmp_path / "theme.toml"
    theme.write_text('page_size = "A4"\n')
    text = source(build_notebook(result, tmp_path / "out", theme_file=theme))
    assert f"THEME_FILE = Path({str(theme.resolve())!r})" in text


def test_the_notebook_is_its_template_with_every_marker_filled(tmp_path):
    """#144: the cells are notebook_template.py's, in order (an integration's where `# @cells`
    is), with no marker or placeholder left
    """
    from nav_qa_processing import notebook

    template = notebook._template()
    extras = NotebookExtras(cells=[new_markdown_cell("## Extra")])
    built = build_notebook(cruise(tmp_path / "in"), tmp_path / "out", extras)
    kinds = [kind for kind, lines in template if "# @cells" not in lines]
    at = next(i for i, (_, lines) in enumerate(template) if "# @cells" in lines)
    kinds.insert(at, "markdown")
    assert [cell.cell_type for cell in built.cells] == kinds
    text = source(built)
    assert "# @" not in text and "# fmt:" not in text
    assert not any(f"{{{name}}}" in text for name in ("cruise_id", "nav_format", "versions"))
