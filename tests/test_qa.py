import pytest
from conftest import DATA

from nav_qa_processing import qa
from nav_qa_processing.config import QAConfig

SP2613 = DATA / "SP2613_169851_gnss"
CONFIG = """\
[cruise]
id = "SP2613"
depart_date = 2026-07-09
arrive_date = 2026-07-09
doi = "doi:10.7284/911454"

[fileset]
doi = "doi:10.7284/169851"

[nav]
format = "nav29"

[geocsv]
attribution = "R/V Sproul"
"""


def needs(directory):
    if not (directory / "data").is_dir():
        pytest.skip(f"{directory.name} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
                    + " ".join(directory.name.split("_")[:2]))  # fmt: skip


def test_sp2613_from_a_config_toml(tmp_path):
    needs(SP2613)
    path = tmp_path / "SP2613.toml"
    path.write_text(CONFIG)
    result = qa.run_qa(SP2613, path)
    assert result.config_path == path and result.errors == []
    assert result.nav.files == ["SP2613_mru_trimble_bx992-2026-07-09.txt"]  # the date window
    assert len(result.bestres) == 56_925 and not result.qc.flags.any()
    assert (len(result.one_minute), len(result.control)) == (475, 5)
    assert result.source.cruise_doi == "doi:10.7284/911454"
    assert result.source.attribution == "R/V Sproul"
    assert "SP2613: 56925 fixes (nav29) from 1 file" in str(result)


def test_without_a_config_toml_the_directory_names_the_cruise():
    needs(SP2613)
    result = qa.run_qa(SP2613, QAConfig(nav_format="nav29"))
    assert result.config.cruise_id == "SP2613_169851_gnss"
    assert len(result.nav.files) == 2  # no dates: both files


def test_no_nav_format_says_what_to_set(tmp_path):
    (tmp_path / "a.txt").write_text("")
    with pytest.raises(qa.ConfigError, match=r"set \[nav\] format.*nav29"):
        qa.run_qa(tmp_path)
    with pytest.raises(ValueError, match="not supported yet"):
        qa.run_qa(tmp_path, QAConfig(nav_format="nav04"))


def test_a_bad_checksum_is_an_error_not_an_exception(tmp_path):
    (tmp_path / "bagit.txt").write_text("")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "gps.txt").write_text("no nav here\n")
    (tmp_path / "manifest-md5.txt").write_text("0" * 32 + "  data/gps.txt\n")
    result = qa.run_qa(tmp_path, QAConfig(nav_format="nav29"))
    assert "data/gps.txt: checksum doesn't match the manifest" in result.errors
    assert any("no ZDA" in error for error in result.errors) and len(result.bestres) == 0
