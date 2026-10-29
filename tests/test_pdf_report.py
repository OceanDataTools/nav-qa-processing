"""The PDF report (#38), on the synthetic cruise of test_plots, with the map layers offline."""

from dataclasses import replace

import matplotlib.pyplot as plt
import numpy as np
import pytest
from pypdf import PdfReader
from reportlab.platypus import Paragraph
from test_plots import cruise

from nav_qa_processing import maps, reports
from nav_qa_processing.fileset import ManifestEntry
from nav_qa_processing.pdf_report import MAX_ERRORS
from nav_qa_processing.theme import Theme


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def pages(path) -> list[str]:
    return [page.extract_text() for page in PdfReader(path).pages]


def test_the_report(tmp_path):
    result = cruise(tmp_path / "in")
    written = reports.write_reports(result, tmp_path / "out")
    pdf = tmp_path / "out" / "TEST_qa_report.pdf"
    assert pdf in written
    text = pages(pdf)
    assert len(text) == 4  # sections follow on without page breaks
    first = text[0]
    words = " ".join(first.split())  # where lines wrap depends on tmp_path's length
    assert "Navigation QA report: TEST" in first
    assert "format nav29" in words and "Generated" in first
    assert result.stats.verdict[1] in words  # the checks in a line (#68)
    assert "Receiver: not in the config; talker IDs: GP (GPS, " in first
    for check in result.stats.checks:
        assert check.name in first
    assert "Checks by file" in first and "nav.txt" in first and "Total" in first
    assert "Skipped" in first
    assert "Statistics" in first and "Most common time between" in " ".join(first.split())
    # One pair of columns, over the fixes that pass QC; R2R's pre-QC ranges are its certificate's
    assert "Number of fixes that pass QC" in first and "Over the fixes that pass QC" in words
    assert "as R2R reports them" not in words
    assert "TEST_bestres.geoCSV" in first
    # The sections in order, each following the last without a page break
    every = " ".join(" ".join(text).split())
    sections = [
        "Quality control",
        "Reader and manifest errors",
        "Gaps of 300 s (00:05:00) or longer",
        "Coverage, gaps and flags",
        "Trackline",
        "Speed and acceleration",
        "Sampling and fix quality",
    ]
    assert [every.index(s) for s in sections] == sorted(every.index(s) for s in sections)
    assert "00:06:41.000" in every  # 499 s to 900 s, as hh:mm:ss (#73)
    assert "Invalid GGA fix" in every
    # Shown without errors too, saying whether there was a manifest to check against
    assert "None reading the nav files. There's no manifest" in every
    # These two share the last page (pdf_report.SERIES_HEIGHT, HISTOGRAMS_HEIGHT)
    assert "Speed and acceleration" in text[-1] and "Sampling and fix quality" in text[-1]
    metadata = PdfReader(pdf).metadata
    assert metadata.title == "Navigation QA report: TEST"
    assert metadata.subject == "Navigation quality assessment"


def test_front_pages_come_first(tmp_path):
    result = cruise(tmp_path / "in")

    def front(lay):
        return [Paragraph("CERTIFICATE FROM AN INTEGRATION", lay.heading)]

    reports.write_reports(result, tmp_path / "out", pdf_front=front, pdf_subject="R2R rating G")
    first = pages(tmp_path / "out" / "TEST_qa_report.pdf")[0]
    assert first.index("CERTIFICATE FROM AN INTEGRATION") < first.index("Checks")
    assert PdfReader(tmp_path / "out" / "TEST_qa_report.pdf").metadata.subject == "R2R rating G"


def test_the_theme_sets_the_page_logo_and_footer(tmp_path):
    result = cruise(tmp_path / "in")
    logo = tmp_path / "logo.png"
    plt.imsave(logo, np.ones((20, 40, 3)))
    theme = Theme(page_size="A4", logo=logo, organization="Example Institution")
    reports.write_reports(result, tmp_path / "out", theme=theme)
    reader = PdfReader(tmp_path / "out" / "TEST_qa_report.pdf")
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)
    assert "Example Institution · Navigation QA report: TEST" in reader.pages[0].extract_text()


def test_long_error_lists_are_counted(tmp_path):
    result = cruise(tmp_path / "in")
    result = replace(result, errors=[f"file{i}: something" for i in range(MAX_ERRORS + 5)])
    reports.write_reports(result, tmp_path / "out")
    text = "".join(pages(tmp_path / "out" / "TEST_qa_report.pdf"))
    assert f"{MAX_ERRORS + 5} errors" in text and "and 5 more" in text
    assert f"file{MAX_ERRORS + 1}" not in text


def test_no_errors_against_a_manifest(tmp_path):
    result = cruise(tmp_path / "in")
    manifest = [ManifestEntry(path="nav.txt", md5="0" * 32)]
    result = replace(result, fileset=replace(result.fileset, manifest=manifest), errors=[])
    reports.write_reports(result, tmp_path / "out", notebook=False)
    text = " ".join(" ".join(pages(tmp_path / "out" / "TEST_qa_report.pdf")).split())
    assert "None reading the nav files or checking them against the manifest." in text
