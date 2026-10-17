from datetime import date

import pytest

from nav_qa_processing.config import (
    Extent,
    QAConfig,
    Thresholds,
    config_to_toml,
    load_config,
    with_thresholds,
)

FULL = """\
[cruise]
id = "SP2613"
name = "MPL Internship Program Day at Sea"
vessel_name = "Robert Gordon Sproul"
depart_date = 2026-07-09
arrive_date = 2026-07-09
doi = "doi:10.7284/911454"
vessel_id = "32QU"                 # R2R's: ignored by core

[cruise.extent]
westernmost = -117.38
easternmost = -117.22
southernmost = 32.59
northernmost = 32.71

[cruise.depart_port]               # R2R's: ignored by core
name = "San Diego"

[fileset]
id = "169851"
doi = "doi:10.7284/169851"

[nav]
format = "nav29"
files = "data/*.txt"

[provenance]
contact_institution = "Scripps Institution of Oceanography"

[geocsv]
attribution = "R/V Sproul"

[openvdm]                           # the site TOML's: ignored by core
output_extra_directory = "NAV_QA"

[thresholds]
max_accel = 1.5
gps_end = 2040-01-01
"""


def test_load_a_full_config(tmp_path):
    path = tmp_path / "SP2613.toml"
    path.write_text(FULL)
    config = load_config(path)
    assert config.cruise_id == "SP2613" and config.vessel_name == "Robert Gordon Sproul"
    assert config.depart_date == date(2026, 7, 9) and config.arrive_date == date(2026, 7, 9)
    assert config.cruise_doi == "doi:10.7284/911454" and config.fileset_doi == "doi:10.7284/169851"
    assert (config.nav_format, config.nav_files) == ("nav29", "data/*.txt")
    assert config.attribution == "R/V Sproul"
    assert config.extent == Extent(-117.38, -117.22, 32.59, 32.71)
    assert config.thresholds.max_accel == 1.5 and config.thresholds.gps_end == date(2040, 1, 1)
    assert config.thresholds.max_gap == Thresholds().max_gap


def test_everything_is_optional(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("")
    assert load_config(path) == QAConfig()


def test_round_trip(tmp_path):
    path = tmp_path / "SP2613.toml"
    path.write_text(FULL)
    config = load_config(path)
    again = tmp_path / "again.toml"
    again.write_text(config_to_toml(config))
    assert load_config(again) == config
    assert str(config) == config_to_toml(config).rstrip("\n")


def test_unknown_thresholds_are_an_error(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[thresholds]\nmax_sped = 9.0\n")
    with pytest.raises(ValueError, match="unknown thresholds: max_sped"):
        load_config(path)


def test_external_manifest_path_is_relative_to_the_config(tmp_path):
    path = tmp_path / "cfg.toml"
    path.write_text('[fileset]\nmanifest = "md5.txt"\n')
    assert load_config(path).manifest_path == (tmp_path / "md5.txt").resolve()


def test_thresholds_print_with_units_and_changes():
    text = str(Thresholds(max_accel=1.5))
    assert "max_accel: 1.5 m s-2 (default 1)" in text and "max_gap: 300 s" in text
    assert str(with_thresholds(QAConfig(), stale_window=12).thresholds).count("default") == 1


def test_extent_across_the_antimeridian():
    box = Extent(178.0, -178.0, -10.0, 10.0)
    assert box.crosses_antimeridian and box.contains(0.0, 179.5) and not box.contains(0.0, 0.0)
    assert "across the antimeridian" in str(box)


@pytest.mark.parametrize(
    "lons, lats, expected",
    [
        ([], [], None),
        ([-117.3], [32.6], Extent(-117.3, -117.3, 32.6, 32.6)),
        ([-117.4, -117.2, -117.3], [32.8, 32.5, 32.6], Extent(-117.4, -117.2, 32.5, 32.8)),
        # Across the antimeridian: the box leaves out the largest gap, not the one through 180
        ([178.0, 179.5, -179.5, -178.0], [-20, -19, -16, -15], Extent(178.0, -178.0, -20, -15)),
        ([179.0, 180.0, -179.0], [0, 0, 0], Extent(179.0, -179.0, 0, 0)),  # 180 is -180
        # A box the long way round is still the smaller one: from Greenwich, east to 170
        ([0.0, 90.0, 170.0], [0, 0, 0], Extent(0.0, 170.0, 0, 0)),
    ],
)
def test_extent_around(lons, lats, expected):
    assert Extent.around(lons, lats) == expected
