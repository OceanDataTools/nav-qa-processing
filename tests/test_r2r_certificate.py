"""R2R's certificate in the PDF and notebook (#40): what it says is what the QA 2.0 XML says."""

import xml.etree.ElementTree as ET
from datetime import date

import nbformat
import pytest
from pypdf import PdfReader
from reportlab.platypus import SimpleDocTemplate
from test_plots import cruise
from test_r2r_references import ours

from nav_qa_processing import maps
from nav_qa_processing.integrations.r2r.certificate import (
    RATING_NAMES,
    bound_text,
    certificate,
    result_text,
)
from nav_qa_processing.integrations.r2r.config import CruiseConfig, Port
from nav_qa_processing.integrations.r2r.reports import (
    notebook_extras,
    run_r2r,
    write_r2r_reports,
)
from nav_qa_processing.integrations.r2r.xml_report import R2R_NAMESPACE, build_qa_xml
from nav_qa_processing.notebook import build_notebook
from nav_qa_processing.pdf_report import layout

NS = {"r2r": R2R_NAMESPACE}


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def synthetic(tmp_path):
    """test_plots' cruise, rated R for its 400 s gap in 20 min, with a config built in Python"""
    cruise(tmp_path / "in")
    config = CruiseConfig(
        cruise_id="TEST",
        nav_format="nav29",
        depart_date=date(2026, 7, 9),
        arrive_date=date(2026, 7, 9),
        fileset_id="1",
        vessel_id="32QU",
        depart_port=Port(name="San Diego"),
        arrive_port=Port(name="San Diego"),
    )
    return run_r2r(tmp_path / "in", config)


def xml_certificate(run) -> tuple[str, dict[str, tuple[str, str, list[str]]]]:
    """The XML's rating, and each test's rating, result and bounds as written"""
    root = build_qa_xml(run.result, run.r2r).getroot()
    tests = {
        t.get("name"): (
            t.findtext("r2r:rating", "", NS),
            t.findtext("r2r:test_result", "", NS),
            [b.text for b in t.findall("r2r:bounds/r2r:bound", NS)],
        )
        for t in root.findall("r2r:certificate/r2r:tests/r2r:test", NS)
    }
    return root.findtext("r2r:certificate/r2r:rating", "", NS), tests


def certificate_text(run, path) -> str:
    """The certificate page alone, as text"""
    SimpleDocTemplate(str(path)).build(certificate(run.result, run.r2r)(layout()))
    return " ".join(page.extract_text() for page in PdfReader(path).pages)


def test_the_certificate_follows_the_title(tmp_path):
    run = synthetic(tmp_path)
    assert run.r2r.rating == "R"
    write_r2r_reports(run, tmp_path / "out")
    reader = PdfReader(tmp_path / "out" / "TEST_qa_report.pdf")
    text = [page.extract_text() for page in reader.pages]
    assert len(text) == 6  # the certificate's page, then the core report's five
    first = text[0]
    assert first.index("Navigation QA report: TEST_1") < first.index("R2R rating: RED (R)")
    assert "R2R fileset 1 · vessel ID 32QU · San Diego to San Diego" in first
    for test in run.r2r.tests:
        assert test.name in first and result_text(test) in first
    assert "TotalTests" in first and "Checks" not in first
    assert "Checks" in text[1]
    assert reader.metadata.subject == "R2R rating R"


@pytest.mark.parametrize(
    "name",
    ["SP2613_169851", "SP2503_167484", "RR2406_161075", "RR2212_151339", "SP2220_150139"],
)
def test_the_certificate_says_what_the_xml_says(name, tmp_path):
    run = ours(name)
    rating, tests = xml_certificate(run)
    assert rating == run.r2r.rating
    text = certificate_text(run, tmp_path / "certificate.pdf")
    assert f"R2R rating: {RATING_NAMES[rating]} ({rating})" in text
    for test in run.r2r.tests:
        written = tests[test.name]
        assert (test.rating, test.text) == written[:2], test.name
        assert [b.text for b in test.bounds] == written[2], test.name
        assert result_text(test) in text
        assert all(bound_text(b) in text for b in test.bounds)


def test_the_notebook_reruns_r2rs_tests_and_writes_the_xml(tmp_path):
    from nbclient import NotebookClient

    run = synthetic(tmp_path)
    notebook = build_notebook(run.result, tmp_path / "out", notebook_extras(run))
    assert notebook.cells[0].source.startswith("# Navigation QA: TEST fileset 1")
    assert "config = loads_cruise_config(CONFIG)" in notebook.cells[2].source
    NotebookClient(notebook, timeout=600).execute()
    code = [c for c in notebook.cells if c.cell_type == "code"]
    errors = [o for c in code for o in c.outputs if o.output_type == "error"]
    assert not errors, errors
    assert "R2R rating R" in "".join(o.get("text", "") for o in code[1].outputs)
    written = "".join(o.get("text", "") for o in code[-1].outputs).splitlines()
    (xml,) = [line for line in written if line.endswith(".xml")]
    root = ET.parse(xml).getroot()
    assert root.findtext("r2r:certificate/r2r:rating", "", NS) == "R"
    assert root.findtext("r2r:filesetinfo/r2r:cruise/r2r:id", "", NS) == "TEST"
    assert any(line.endswith("TEST_qa_report.pdf") for line in written)
    pdf = PdfReader(tmp_path / "out" / "TEST_qa_report.pdf")
    assert pdf.metadata.subject == "R2R rating R"
    assert not (tmp_path / "out" / "TEST_qa.ipynb").exists()


def test_execute_notebook_writes_one_xml(tmp_path):
    run = synthetic(tmp_path)
    write_r2r_reports(run, tmp_path / "out", execute_notebook=True)
    assert len(list((tmp_path / "out").glob("*.xml"))) == 1
    notebook = nbformat.read(tmp_path / "out" / "TEST_qa.ipynb", as_version=4)
    assert notebook.cells[-1].outputs == []
