import io
import json
import tarfile
from dataclasses import fields, replace

import pytest
from conftest import CONFIGS, FIXTURES

from nav_qa_processing.integrations.r2r import catalog, download
from nav_qa_processing.integrations.r2r.config import CruiseConfig, load_cruise_config

REPORT = FIXTURES / "SP2613_169851_r2r_qa.2.0.xml"
REPORT_URL = "https://service.rvdata.us/data/cruise/SP2613/fileset/169851/qa/SP2613_qa.xml"

# R2R's API records for SP2613 as served on 2026-10-14, trimmed to what's used
CRUISE = {
    "cruise_id": "SP2613",
    "cruise_name": "MPL Internship Program Day at Sea",
    "cruise_doi": "10.7284/911454",
    "vessel_ices_code": "32QU",
    "vessel_id": "Robert Gordon Sproul",
    "vessel_name": "Robert Gordon Sproul",
    "operator_id": "edu.ucsd.sio",
    "scheduler_id": "org.unols",
    "waterbody_name": None,
    "depart_date": "2026-07-09",
    "depart_port_id": "100055",
    "depart_port_name": "San Diego",
    "arrive_date": "2026-07-09",
    "arrive_port_id": "100055",
    "arrive_port_name": "San Diego",
    "chief_scientist": "Pereira, Filipe",
    "longitude_min": "-117.3773103608",
    "longitude_max": "-117.2262308092",
    "latitude_min": "32.5997440998",
    "latitude_max": "32.7053695673",
}
FILESETS = [
    {"fileset_id": 169847, "device_type": "ctd", "label": "SP2613 ctd (Seabird SBE-911+)"},
    {
        "fileset_id": 169851,
        "device_type": "gnss",
        "label": "SP2613 gnss (Trimble BX992)",
        "fileset_doi": "10.7284/169851",
        "format_id": 100150,
        "device_id": 101253,
        "make_id": "com.trimble",
        "model_name": "BX992",
        "download_url": "https://service.rvdata.us/data/cruise/SP2613/fileset/169851",
    },
]
DEVICE = {"id": "101253", "make_id": "com.trimble", "model_id": "100542", "model_name": "BX992"}
QA_INFO = [
    {
        "documents": json.dumps(
            [
                {"uri": REPORT_URL, "file_name": "SP2613_169851_qa.2.0.2026-07-31T20:05:56Z.xml"},
                {"uri": "https://service.rvdata.us/info/format/100150", "file_name": ""},
            ]
        )
    }
]


@pytest.fixture
def r2r(monkeypatch):
    """R2R's services, offline"""

    def get(url, service="R2R"):
        if "/api/cruise/" in url:
            data = [CRUISE] if "SP2613" in url else None
        elif "/api/fileset/" in url:
            data = FILESETS
        elif "/api/qa_info/" in url:
            data = QA_INFO
        elif "/info/vessel/32QU/device/101253" in url:
            return json.dumps(DEVICE).encode()
        elif url == REPORT_URL:
            return REPORT.read_bytes()
        else:
            raise AssertionError(f"unexpected {url}")
        return json.dumps({"data": data}).encode()

    monkeypatch.setattr(catalog, "_get", get)


def test_nav_format_from_r2rs_format_id():
    assert catalog.nav_format(100150) == "nav29"
    assert catalog.nav_format("100010") == "nav1"
    assert catalog.nav_format(1) == ""


def filesetinfo(config: CruiseConfig) -> dict:
    """What the XML's provenance and filesetinfo are written from"""
    names = [f.name for f in fields(CruiseConfig)]
    skip = {"thresholds", "manifest_path", "cruise_pi", "vessel_name", "cruise_doi"}
    skip |= {"fileset_doi", "attribution", "nav_files"}
    info = {name: getattr(config, name) for name in names if name not in skip}
    # the report writes the extent to 7 decimals, the API has 10
    info["extent"] = tuple(round(getattr(config.extent, f.name), 7) for f in fields(config.extent))
    return info


def test_config_from_r2rs_qa_report():
    config = catalog.config_from_qa_report(REPORT)
    assert config.identifier == "SP2613_169851"
    assert (config.date_modified, config.fileset_date_created) == (
        "2026-07-31T04:28:14Z",
        "2026-07-22",
    )
    assert config.depart_port.latitude == 32.71667 and config.depart_port.state_id == "ca"
    assert (config.format_alias, config.nav_format) == ("starc_raw", "nav29")
    assert config.device.model_id == "100542" and config.contact_institution_id == "us.rvdata"


def test_catalog_config_reproduces_r2rs_filesetinfo(r2r):
    config = catalog.config_from_r2r_catalog("SP2613", 169851)
    assert filesetinfo(config) == filesetinfo(catalog.config_from_qa_report(REPORT))
    assert config.cruise_doi == "doi:10.7284/911454" and config.fileset_doi == "doi:10.7284/169851"
    assert config.cruise_pi == "Pereira, Filipe"
    assert config.attribution == catalog.R2R_ATTRIBUTION
    # and the committed config is what config-from-catalog wrote
    assert filesetinfo(load_cruise_config(CONFIGS / "SP2613_169851.toml")) == filesetinfo(config)


def test_catalog_api_alone_lacks_what_only_the_report_has(r2r):
    config = catalog.config_from_r2r_catalog("SP2613", 169851, use_qa_report=False)
    assert config.depart_port.port_id == "100055" and config.depart_port.latitude is None
    assert (config.format_alias, config.date_issued, config.fileset_date_created) == ("", "", "")
    assert config.device.model_id == "100542" and config.nav_format == "nav29"


def test_catalog_errors_name_the_gnss_filesets(r2r):
    with pytest.raises(catalog.R2RError, match="R2R has no cruise XX0000"):
        catalog.config_from_r2r_catalog("XX0000", 1)
    with pytest.raises(catalog.R2RError, match=r"169847 is SP2613 ctd.*169851 \(SP2613 gnss"):
        catalog.config_from_r2r_catalog("SP2613", 169847)


def test_fill_missing_keeps_what_is_set():
    config = catalog.config_from_qa_report(REPORT)
    bare = replace(config, format_alias="", depart_port=replace(config.depart_port, latitude=None))
    bare = replace(bare, operator_id="other")
    filled = catalog.fill_missing(bare, config)
    assert filled.format_alias == "starc_raw" and filled.depart_port.latitude == 32.71667
    assert filled.operator_id == "other"


def test_report_without_filesetinfo(tmp_path):
    path = tmp_path / "old.xml"
    path.write_text(
        '<r2r:qareport xmlns:r2r="https://service.rvdata.us/schema/r2r-2.0">'
        "<r2r:filesetinfo><r2r:cruise/><r2r:fileset/></r2r:filesetinfo></r2r:qareport>"
    )
    with pytest.raises(catalog.R2RError, match="no cruise information"):
        catalog.config_from_qa_report(path)


# -- download --


def test_extract_bag_finds_the_nested_bag(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, text in (
            ("SP2613/169851/bagit.txt", b"BagIt-Version: 1.0\n"),
            ("SP2613/169851/data/a.txt", b"$GPGGA\n"),
            ("SP2613/README", b"outside the bag\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text))
    assert download.extract_bag(archive.getvalue(), tmp_path) == 2
    assert (tmp_path / "data" / "a.txt").read_bytes() == b"$GPGGA\n"
    assert not (tmp_path / "README").exists()


@pytest.mark.parametrize(
    ("href", "child"),
    [("data/", True), ("a.txt", True), ("../", False), ("http://x/", False), ("%2e%2e/", False)],
)
def test_only_links_under_a_listing_are_followed(href, child):
    assert download._is_child(href) is child


def test_qa_report_url_from_qa_info(r2r):
    assert catalog.qa_report_url(169851) == REPORT_URL
