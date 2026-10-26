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


def _gga_file(path, day: str, seconds: int = 60, at_null_island: bool = False):
    """A nav33 file of one fix a second from midnight of `day`, drifting east (or at 0, 0)"""
    lines = []
    for s in range(seconds):
        where = (
            "0000.00000,N,00000.00000,E"
            if at_null_island
            else (f"2635.03241,N,09331.{21859 + s:05d},W")
        )
        body = f"GPGGA,00{s // 60:02d}{s % 60:02d}.00,{where},2,09,1.1,10.0,M,,M,,"
        checksum = 0
        for char in body:
            checksum ^= ord(char)
        lines.append(f"{day}T00:{s // 60:02d}:{s % 60:02d}.100000Z,${body}*{checksum:02X}")
    path.write_text("\n".join(lines) + "\n")


def _config(depart, arrive):
    return QAConfig(cruise_id="X", nav_format="nav33", depart_date=depart, arrive_date=arrive)


def test_every_file_outside_the_cruise_window_is_said_plainly(tmp_path):
    """#122: the OpenVDM sample's 2012 files for a cruise dated 2026"""
    from datetime import date

    _gga_file(tmp_path / "a.txt", "2012-04-28")
    _gga_file(tmp_path / "b.txt", "2012-04-29")
    result = qa.run_qa(tmp_path, _config(date(2026, 1, 5), date(2026, 1, 20)))
    assert result.warnings == [
        (
            "every nav file is outside the cruise window (2026-01-05 to 2026-01-20): 2 files "
            "with fixes from 2012-04-28 00:00 to 2012-04-29 00:00 UTC; check the cruise's dates"
        )
    ]
    assert "  warning: every nav file is outside the cruise window" in str(result)
    assert result.stats.verdict == ("warn", "No fixes were read, so no check could be made.")
    assert {(c.status, c.detail) for c in result.stats.checks[:-1]} == {("n/a", "no fixes")}
    assert result.qc.notes == []  # no resolution to allow for without fixes


def test_some_files_outside_the_cruise_window_are_named(tmp_path):
    from datetime import date

    _gga_file(tmp_path / "a.txt", "2026-01-04")
    _gga_file(tmp_path / "b.txt", "2026-01-05")
    result = qa.run_qa(tmp_path, _config(date(2026, 1, 5), None))
    assert result.nav.files == ["b.txt"] and len(result.nav.frame) == 60
    assert result.warnings == [
        (
            "1 of 2 nav files are outside the cruise window (from 2026-01-05), with fixes from "
            "2026-01-04 00:00 to 2026-01-04 00:00 UTC: a.txt"
        )
    ]
    assert result.stats.verdict[0] != "n/a"


def test_files_inside_the_cruise_window_give_no_warning(tmp_path):
    from datetime import date

    _gga_file(tmp_path / "a.txt", "2026-01-05")
    assert qa.run_qa(tmp_path, _config(date(2026, 1, 5), date(2026, 1, 5))).warnings == []


def test_fixes_none_of_them_usable(tmp_path):
    from datetime import date

    _gga_file(tmp_path / "a.txt", "2026-01-05", at_null_island=True)
    result = qa.run_qa(tmp_path, _config(date(2026, 1, 5), date(2026, 1, 5)))
    assert len(result.nav.frame) == 60 and result.warnings == []
    assert result.stats.verdict == ("warn", "No fix is usable, so no check could be made.")
    assert {c.detail for c in result.stats.checks[:-1]} == {"no usable fixes"}
    assert result.stats.checks[-1].value == "60"  # every fix flagged


def test_a_run_without_fixes_draws_and_writes_without_warnings(tmp_path):
    import warnings
    from datetime import date

    from nav_qa_processing import plots, reports
    from nav_qa_processing.theme import DEFAULT_THEME

    _gga_file(tmp_path / "a.txt", "2012-04-28")
    result = qa.run_qa(tmp_path, _config(date(2026, 1, 5), date(2026, 1, 20)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for figure in plots.FIGURES.values():
            figure(result, DEFAULT_THEME)
    paths = reports.write_reports(result, tmp_path / "out", notebook=False)
    assert all(path.exists() for path in paths)
