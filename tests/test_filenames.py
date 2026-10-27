"""Cruise IDs that aren't valid file names still give outputs inside -o (#154)."""

import ast
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import nbformat
import pytest

from nav_qa_processing import filenames, geocsv
from nav_qa_processing.cli import main
from nav_qa_processing.clitools import cruise_toml_path
from nav_qa_processing.config import QAConfig
from nav_qa_processing.filenames import report_stem, safe_filename
from nav_qa_processing.integrations.r2r.config import CruiseConfig
from nav_qa_processing.integrations.r2r.xml_report import qa_xml_name
from nav_qa_processing.reports import report_paths

# A cruise ID with a path separator, a parent directory and characters Windows forbids
CRUISE_IDS = [
    ("AT50/12", "AT50_12"),
    ("../escaped", ".._escaped"),
    ('T<S:1 "q" \\', "T_S_1 _q_ _"),
]


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    """report_stem warns once per identifier per process"""
    monkeypatch.setattr(filenames, "_warned", set())


@pytest.mark.parametrize(
    "name, expected",
    [
        ("SP2613", "SP2613"),
        ("SP2613_169851", "SP2613_169851"),
        ("SP2613_seapath", "SP2613_seapath"),
        ("BH19-30", "BH19-30"),
        ("AT50/12", "AT50_12"),
        ("../escaped", ".._escaped"),
        ("a\\b", "a_b"),
        ('T<S:1|"x"?*', "T_S_1__x___"),
        ("tab\there", "tab_here"),
        ("trailing. ", "trailing"),
        ("CON", "_CON"),
        ("con.toml", "_con.toml"),
        ("COM1", "_COM1"),
        ("CONTROL", "CONTROL"),
        ("..", "_"),
        ("", "_"),
        ("  ", "_"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_report_stem_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="nav_qa_processing.filenames"):
        assert report_stem("SP2613") == "SP2613"
        assert report_stem("AT50/12") == "AT50_12"
        assert report_stem("AT50/12") == "AT50_12"
    assert [r.getMessage() for r in caplog.records] == [
        "'AT50/12' isn't a valid file name on every platform; the files are named AT50_12 instead"
    ]


def test_cruise_toml_path():
    assert cruise_toml_path(Path("configs"), "AT50/12") == Path("configs") / "AT50_12.toml"
    assert cruise_toml_path(Path("configs"), "NUL") == Path("configs") / "_NUL.toml"


def test_r2r_xml_name():
    config = CruiseConfig(
        cruise_id="AT50/12",
        fileset_id="1",
        depart_date=date(2026, 7, 9),
        arrive_date=date(2026, 7, 10),
    )
    finished = datetime(2026, 7, 31, 20, 5, 56, tzinfo=UTC)
    assert qa_xml_name(config, finished) == "AT50_12_1_qa.2.0.2026-07-31T200556Z.xml"


def test_a_feed_name_from_python_is_made_a_file_name():
    """Config TOMLs and the site TOML allow only letters, digits, _ and - in [[nav]] names, but
    a QAConfig made in Python can have any
    """
    config = QAConfig(cruise_id="SP2613", nav_name="sea/path", nav_primary=False)
    paths = report_paths(Path("out"), config.stem)
    assert paths["pdf"] == Path("out") / "SP2613_sea_path_qa_report.pdf"
    assert {path.parent for path in paths.values()} == {Path("out")}


def write_config(path: Path, cruise_id: str, cruise: str = "", nav: str = "") -> Path:
    # A TOML literal string keeps the ID exactly, backslashes and quotes included
    path.write_text(
        f"[cruise]\nid = '''{cruise_id}'''\n{cruise}\n[nav]\nformat = \"nav33\"\n{nav}"
    )
    return path


def assert_only_in(output: Path, names: set[str], tmp_path: Path) -> None:
    """Everything is directly in output: no subdirectory from a /, nothing outside it from .."""
    assert {p.name for p in output.iterdir()} == names
    assert {p.name for p in tmp_path.iterdir()} == {"raw", "config.toml", "out"}


@pytest.mark.parametrize("cruise_id, stem", CRUISE_IDS)
def test_run_writes_inside_output(cruise_id, stem, tmp_path):
    (tmp_path / "raw").mkdir()
    config = write_config(tmp_path / "config.toml", cruise_id)
    output = tmp_path / "out"
    assert main(["run", str(tmp_path / "raw"), "-c", str(config), "-o", str(output)]) == 0

    products = {f"{stem}_{p}.geoCSV" for p in ("bestres", "1min", "control")}
    reports = {f"{stem}_plots", f"{stem}_qa_report.pdf", f"{stem}_qa.ipynb"}
    assert_only_in(output, products | reports, tmp_path)

    # The ID stays as given where it's shown
    assert geocsv.read(output / f"{stem}_bestres.geoCSV").keywords["cruise_id"] == cruise_id
    notebook = nbformat.read(output / f"{stem}_qa.ipynb", as_version=4)
    assert cruise_id in notebook.cells[0].source
    for cell in notebook.cells:  # its code still parses with any characters in the ID
        if cell.cell_type == "code":
            ast.parse("\n".join(line for line in cell.source.splitlines() if line[:1] != "%"))


@pytest.mark.parametrize("cruise_id, stem", CRUISE_IDS)
def test_r2r_run_writes_inside_output(cruise_id, stem, tmp_path):
    (tmp_path / "raw").mkdir()
    dates = "depart_date = 2026-07-09\narrive_date = 2026-07-10\n"
    config = write_config(tmp_path / "config.toml", cruise_id, dates)
    output = tmp_path / "out"
    args = ["r2r", "run", str(config), str(tmp_path / "raw"), "-o", str(output), "--r2rnav"]
    assert main(args) == 0

    names = {p.name for p in output.iterdir()}
    (xml,) = [name for name in names if name.endswith(".xml")]
    assert xml.startswith(f"{stem}_gnss_qa.2.0.")
    r2rnav = {f"{stem}_{p}.r2rnav" for p in ("bestres", "1min", "control")}
    assert r2rnav < names and f"{stem}_qa_report.pdf" in names
    assert_only_in(output, names, tmp_path)
    root = ET.parse(output / xml).getroot()
    (identifier,) = [e.text for e in root if e.tag.endswith("}identifier")]
    assert identifier == f"{cruise_id}_gnss_qa"
