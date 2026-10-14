"""Our QA 2.0 XML against R2R's: the same structure, test names, ratings and values, but for the
differences docs/navmanager-differences.md lists (X1, A5)."""

import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime

import pytest
from conftest import CONFIGS, DATA, FIXTURES

from nav_qa_processing.integrations.r2r.config import CruiseConfig, load_cruise_config
from nav_qa_processing.integrations.r2r.rules import run_r2r_tests
from nav_qa_processing.integrations.r2r.xml_report import (
    R2R_NAMESPACE,
    qa_xml_name,
    write_qa_xml,
)
from nav_qa_processing.qa import run_qa

DAY = date(2026, 7, 9)

NS = {"r2r": R2R_NAMESPACE}
UNITS = {
    "Percent": "percent",
    "Count": "1",
    "Dimensionless": "1",
    "m/s": "m s-1",
    "m/s^2": "m s-2",
}
# R2R measures accelerations differently (A5)
ACCELERATIONS = ("PercentUnreasonableAccelerations", "HorizontalAcceleration")


def ours(name, tmp_path_factory):
    cruise, fileset = name.split("_")
    directory = DATA / f"{name}_gnss"
    if not (directory / "r2r_qa.2.0.xml").exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run scripts/fetch_r2r_fileset.py {cruise} {fileset}"
        )
    result = run_qa(directory, load_cruise_config(CONFIGS / f"{name}.toml"))
    path = write_qa_xml(
        result, run_r2r_tests(result.stats), tmp_path_factory.mktemp(name) / "qa.xml"
    )
    return ET.parse(path).getroot(), ET.parse(directory / "r2r_qa.2.0.xml").getroot()


@pytest.fixture(scope="module")
def sp2613(tmp_path_factory):
    return ours("SP2613_169851", tmp_path_factory)


def paths(element, prefix=""):
    """Every element's path, with its name attribute, in document order"""
    tag = element.tag.split("}")[-1]
    name = element.get("name")
    path = f"{prefix}/{tag}" + (f"[{name}]" if name else "")
    result = [path]
    for child in element:
        if isinstance(child.tag, str):
            result += paths(child, path)
    return result


def without_duplicate_id(found):
    """R2R's cruise repeats <r2r:id> (X1)"""
    first = found.index("/qareport/filesetinfo/cruise/id")
    return found[: first + 1] + [p for p in found[first + 1 :] if p != found[first]]


def certificate(root):
    tests = {
        t.get("name"): (
            t.find("r2r:rating", NS).text,
            float(t.find("r2r:test_result", NS).text),
            t.find("r2r:test_result", NS).get("uom"),
            [
                (b.get("name"), float(b.text), UNITS.get(b.get("uom"), b.get("uom")))
                for b in t.findall("r2r:bounds/r2r:bound", NS)
            ],
        )
        for t in root.findall("r2r:certificate/r2r:tests/r2r:test", NS)
    }
    infos = {
        i.get("name"): (int(i.text), UNITS.get(i.get("uom"), i.get("uom")))
        for i in root.findall("r2r:certificate/r2r:infos/r2r:info", NS)
    }
    ranges = {
        s.get("name"): [
            (r.get("name"), float(r.text), UNITS.get(r.get("uom"), r.get("uom")))
            for r in s.findall("r2r:ranges/r2r:range", NS)
        ]
        for s in root.findall("r2r:filesetinfo_supplements/r2r:filesetinfo_supplemental", NS)
    }
    rating = root.find("r2r:certificate/r2r:rating", NS).text
    return rating, tests, infos, ranges


def assert_same_certificate(ours_, r2rs, accelerations_differ=True):
    rating, tests, infos, ranges = certificate(ours_)
    r2r_rating, r2r_tests, r2r_infos, r2r_ranges = certificate(r2rs)
    assert rating == r2r_rating
    assert list(tests) == list(r2r_tests)
    assert infos == r2r_infos
    for name, (test_rating, result, uom, bounds) in tests.items():
        r2r_test_rating, r2r_result, r2r_uom, r2r_bounds = r2r_tests[name]
        assert (test_rating, bounds) == (r2r_test_rating, r2r_bounds), name
        assert uom == UNITS.get(r2r_uom, r2r_uom)
        if not (accelerations_differ and name in ACCELERATIONS):
            assert result == pytest.approx(r2r_result, abs=0.005), name
    assert list(ranges) == list(r2r_ranges)
    for name in ranges:
        if name not in ACCELERATIONS:
            assert ranges[name] == r2r_ranges[name], name


def test_sp2613_has_r2rs_structure(sp2613):
    root, r2rs = sp2613
    assert paths(root) == without_duplicate_id(paths(r2rs))
    assert paths(ET.parse(FIXTURES / "SP2613_169851_r2r_qa.2.0.xml").getroot()) == paths(r2rs)


def test_sp2613_filesetinfo_and_manifest_equal_r2rs(sp2613):
    root, r2rs = sp2613

    def texts(element, path):
        return [(e.tag, (e.text or "").strip(), e.attrib) for e in element.find(path, NS).iter()]

    for section in ("r2r:filesetinfo/r2r:fileset", "r2r:manifest", "r2r:references"):
        assert texts(root, section) == texts(r2rs, section), section
    cruise = [(e.tag, e.text) for e in root.find("r2r:filesetinfo/r2r:cruise", NS).iter()]
    r2r_cruise = [(e.tag, e.text) for e in r2rs.find("r2r:filesetinfo/r2r:cruise", NS).iter()]
    assert cruise == r2r_cruise[:6] + r2r_cruise[7:]  # but for R2R's second <r2r:id>
    for field in ("cruise_id", "fileset_id", "distro_type", "contact_email", "device_type"):
        path = f"r2r:provenance/r2r:{field}"
        assert root.find(path, NS).text == r2rs.find(path, NS).text


def test_sp2613_certificate_equals_r2rs(sp2613):
    """G on every test; the accelerations agree at 0.00 %, their ranges differ (A5)"""
    assert_same_certificate(*sp2613)
    _, tests, _, _ = certificate(sp2613[0])
    assert tests["PercentUnreasonableAccelerations"][1] == 0.0


def test_sp2503_certificate_equals_r2rs(tmp_path_factory):
    """R overall: R on completeness (47.34 %), Y on the 48,625.5 s gap"""
    root, r2rs = ours("SP2503_167484", tmp_path_factory)
    assert_same_certificate(root, r2rs)
    assert paths(root) == without_duplicate_id(paths(r2rs))


def test_sp2424_certificate_equals_r2rs(tmp_path_factory):
    """R2R's report has an empty filesetinfo, so only the certificate and ranges compare"""
    root, r2rs = ours("SP2424_161900", tmp_path_factory)
    assert_same_certificate(root, r2rs)


def test_identifier_and_process(sp2613):
    root, _ = sp2613
    assert root.find("r2r:identifier", NS).text == "SP2613_169851_qa"
    assert root.find("r2r:provenance/r2r:updates/r2r:update/r2r:process", NS).text == (
        "nav-qa-processing"
    )


def test_r2rs_file_name():
    config = CruiseConfig(
        cruise_id="SP2613", fileset_id="169851", depart_date=DAY, arrive_date=DAY
    )
    finished = datetime(2026, 7, 31, 20, 5, 56, tzinfo=UTC)
    assert qa_xml_name(config, finished) == "SP2613_169851_qa.2.0.2026-07-31T20:05:56Z.xml"
