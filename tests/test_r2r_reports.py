"""`r2r run`: R2R's three products and report, by R2R's names and structure."""

import pytest
from conftest import CONFIGS, DATA, FIXTURES

from nav_qa_processing import geocsv
from nav_qa_processing.cli import main
from nav_qa_processing.integrations.r2r.catalog import R2R_REPOSITORY

SP2613 = DATA / "SP2613_169851_gnss"


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    if not (SP2613 / "products").exists():
        pytest.skip(
            "SP2613 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset SP2613 169851"
        )
    output = tmp_path_factory.mktemp("r2r")
    assert (
        main(["r2r", "run", str(CONFIGS / "SP2613_169851.toml"), str(SP2613), "-o", str(output)])
        == 0
    )
    return output


def test_r2r_names(written):
    names = {p.name for p in written.iterdir()}
    products = {
        "SP2613_1min.geoCSV",
        "SP2613_bestres.geoCSV",
        "SP2613_control.geoCSV",
        "SP2613_plots",
        "SP2613_qa_report.pdf",
        "SP2613_qa.ipynb",
    }
    assert products < names
    (xml,) = names - products
    assert xml.startswith("SP2613_169851_qa.2.0.") and xml.endswith("Z.xml")


@pytest.mark.parametrize("product", ["bestres", "1min", "control"])
def test_products_have_r2rs_structure_and_source(written, product):
    ours = geocsv.read(written / f"SP2613_{product}.geoCSV")
    r2rs = geocsv.read(SP2613 / "products" / "data" / f"SP2613_{product}.geoCSV")
    assert list(ours.keywords) == list(r2rs.keywords) and ours.columns == r2rs.columns
    for keyword in (
        "attribution",
        "source_repository",
        "source_event",
        "source_dataset",
        "cruise_id",
    ):
        assert ours.keywords[keyword] == r2rs.keywords[keyword], keyword
    assert ours.keywords["source_repository"] == R2R_REPOSITORY
    assert len(ours.records) == len(r2rs.records)


def test_products_and_report_share_the_run_time(written):
    xml = next(written.glob("*.xml"))
    created = geocsv.read(written / "SP2613_control.geoCSV").keywords["creation_date"]
    assert xml.name == f"SP2613_169851_qa.2.0.{created.replace(':', '')}.xml"


def test_a_second_run_refuses_to_overwrite(written, capsys):
    argv = ["r2r", "run", str(CONFIGS / "SP2613_169851.toml"), str(SP2613), "-o", str(written)]
    assert main(argv) == 1
    assert "use --force" in capsys.readouterr().err


def test_config_from_qa_to_stdout(capsys):
    assert (
        main(["r2r", "config-from-qa", str(FIXTURES / "SP2613_169851_r2r_qa.2.0.xml"), "-o", "-"])
        == 0
    )
    out = capsys.readouterr().out
    assert 'format_alias = "starc_raw"' in out and 'format = "nav29"' in out


def test_fetch_fileset_refuses_an_existing_directory(tmp_path, capsys):
    (tmp_path / "SP2613_169851_gnss").mkdir()
    assert main(["r2r", "fetch-fileset", "SP2613", "169851", "-o", str(tmp_path)]) == 1
    assert "use --force" in capsys.readouterr().err


def test_fileset_id_must_be_an_integer(capsys):
    with pytest.raises(SystemExit):
        main(["r2r", "fetch-fileset", "SP2613", "gnss"])
    assert "not an R2R fileset ID" in capsys.readouterr().err


# -- R2R's HTTP errors are one line, not a traceback (#101) --


def _http_error(code=503, reason="Service Unavailable"):
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "https://service.rvdata.us/api/x", code, reason, {}, io.BytesIO()
    )


def test_config_from_catalog_reports_an_http_error_in_one_line(tmp_path, capsys, monkeypatch):
    from nav_qa_processing.integrations.r2r import catalog

    def unavailable(*args, **kwargs):
        raise _http_error()

    monkeypatch.setattr(catalog.urllib.request, "urlopen", unavailable)
    assert main(["r2r", "config-from-catalog", "SP2613", "169851", "-o", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "Traceback" not in err
    assert (
        "R2R's catalog (service.rvdata.us) answered 503 Service Unavailable; try again later"
        in err
    )
    # -vv adds the traceback
    assert (
        main(["r2r", "config-from-catalog", "SP2613", "169851", "-o", str(tmp_path), "-vv"]) == 1
    )
    assert "Traceback" in capsys.readouterr().err


def test_fetch_fileset_says_what_arrived_when_the_products_fail(tmp_path, capsys, monkeypatch):
    from nav_qa_processing.integrations.r2r import download

    records = {
        "fileset": [{"fileset_id": 105061, "download_url": "https://example.org/bag.tar.gz"}],
        "product": [{"product_type": "r2rnav", "url": "https://example.org/products/"}],
    }
    monkeypatch.setattr(download, "fetch_r2r_records", lambda endpoint, **q: records[endpoint])
    monkeypatch.setattr(download, "download_qa_report", lambda *a: None)

    def bag(opener, url, destination, what="R2R's fileset download"):
        if "products" in url:
            raise download.http_error(_http_error(), what, url)
        return url, 5

    monkeypatch.setattr(download, "download_bag", bag)
    assert main(["r2r", "fetch-fileset", "BH10-17", "105061", "-o", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "Traceback" not in err
    assert "5 raw files are in" in err and "R2R's product bag (example.org) answered 503" in err
    assert "Run again with --force to fetch them" in err


def test_an_unknown_device_answered_with_404_is_no_device(monkeypatch):
    from nav_qa_processing.integrations.r2r import catalog

    def not_found(*args, **kwargs):
        raise _http_error(404, "Not Found")

    monkeypatch.setattr(catalog.urllib.request, "urlopen", not_found)
    assert catalog.fetch_device("33P4", "999999") == {}
