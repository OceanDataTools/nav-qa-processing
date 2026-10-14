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
    products = {"SP2613_1min.geoCSV", "SP2613_bestres.geoCSV", "SP2613_control.geoCSV"}
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
    assert xml.name == f"SP2613_169851_qa.2.0.{created}.xml"


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
