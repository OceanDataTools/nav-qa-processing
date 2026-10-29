"""--no-products: the reports without the GeoCSV products, on run, r2r run and the hook."""

import nbformat
import pytest
from pypdf import PdfReader
from test_plots import cruise
from test_r2r_certificate import synthetic

from nav_qa_processing import maps, reports
from nav_qa_processing.cli import main
from nav_qa_processing.integrations.r2r.reports import write_r2r_reports


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def text(pdf) -> str:
    return " ".join(" ".join(page.extract_text() for page in PdfReader(pdf).pages).split())


def test_the_reports_without_products(tmp_path):
    result = cruise(tmp_path / "in")
    written = reports.write_reports(result, tmp_path / "out", products=False)
    assert not list((tmp_path / "out").glob("*.geoCSV"))
    assert not any(path.suffix == ".geoCSV" for path in written)
    names = {path.name for path in (tmp_path / "out").iterdir()}
    assert names == {"TEST_plots", "TEST_qa_report.pdf", "TEST_qa.ipynb"}
    # The PDF still gives the products' records, the files marked
    pdf = text(tmp_path / "out" / "TEST_qa_report.pdf")
    assert f"TEST_bestres.geoCSV (not written) {len(result.bestres)}" in pdf
    # Re-running the notebook writes the reports as they were, without the products
    notebook = nbformat.read(tmp_path / "out" / "TEST_qa.ipynb", as_version=4)
    assert "products=False" in notebook.cells[-1].source


def test_the_reports_with_products_say_nothing_of_it(tmp_path):
    result = cruise(tmp_path / "in")
    reports.write_reports(result, tmp_path / "out", notebook=False)
    assert len(list((tmp_path / "out").glob("*.geoCSV"))) == 3
    assert "not written" not in text(tmp_path / "out" / "TEST_qa_report.pdf")


def test_run_without_products_leaves_existing_ones_alone(tmp_path):
    """Products it won't write don't stop it, and aren't touched"""
    (tmp_path / "raw").mkdir()
    config = tmp_path / "config.toml"
    config.write_text('[cruise]\nid = "X"\n\n[nav]\nformat = "nav33"\n')
    out = tmp_path / "out"
    out.mkdir()
    (out / "X_bestres.geoCSV").write_text("earlier\n")
    args = ["run", str(tmp_path / "raw"), "-c", str(config), "-o", str(out)]
    assert main([*args, "--no-products"]) == 0
    assert (out / "X_bestres.geoCSV").read_text() == "earlier\n"
    assert (out / "X_qa_report.pdf").exists() and not (out / "X_1min.geoCSV").exists()
    assert main(args) == 1  # with the products, the earlier one is in the way


def test_r2r_without_products(tmp_path):
    run = synthetic(tmp_path)
    written = write_r2r_reports(run, tmp_path / "out", r2rnav=True, products=False)
    suffixes = {path.suffix for path in written}
    assert ".xml" in suffixes and ".r2rnav" in suffixes and ".geoCSV" not in suffixes
    assert not list((tmp_path / "out").glob("*.geoCSV"))
