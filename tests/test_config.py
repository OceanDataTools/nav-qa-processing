from datetime import date

import pytest

from nav_qa_processing.config import (
    Extent,
    NavDevice,
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

[nav.device]
make = "Trimble"
model = "BX992"
location = "main mast"

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
    assert config.nav_device == NavDevice(make="Trimble", model="BX992", location="main mast")
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


def test_nav_device():
    assert str(NavDevice("Trimble", "BX992")) == "Trimble BX992"
    assert str(NavDevice("Applanix", "POS MV", "1234", "main lab")) == (
        "Applanix POS MV (serial number 1234, main lab)"
    )
    assert str(NavDevice(location="mast")) == "unnamed device (mast)"
    assert not NavDevice() and NavDevice(model="BX992")
    assert "[nav.device]" not in config_to_toml(QAConfig(nav_format="nav29"))


def test_unknown_nav_device_keys_are_an_error(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[nav.device]\nmodle = "BX992"\n')
    with pytest.raises(ValueError, match=r"unknown \[nav.device\] keys: modle"):
        load_config(path)


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


def test_unknown_keys_say_where_they_belong():
    """#123: cruise keys at the top level, a typo and a key in the wrong table"""
    from nav_qa_processing.config import loads_config

    text = 'cruise_id = "X"\ndepart_date = 2026-01-05\n[nav]\nformat = "nav33"\nfiels = "*"\n'
    with pytest.raises(ValueError) as error:
        loads_config(text)
    assert str(error.value) == (
        "config: unknown keys: cruise_id (top level): did you mean [cruise] id?; "
        "depart_date (top level): did you mean [cruise] depart_date?; "
        "[nav] fiels: did you mean files?"
    )
    with pytest.raises(ValueError, match=r"unknown key: \[fileset\] attribution: did you mean "):
        loads_config('[fileset]\nattribution = "x"\n')
    with pytest.raises(ValueError, match=r"unknown key: \[cruise.extent\] west$"):
        loads_config("[cruise.extent]\nwest = 1\n")
    with pytest.raises(ValueError, match=r"unknown key: \[nav\] colour$"):
        loads_config('[[nav]]\nname = "a"\nformat = "nav29"\ncolour = 1\n')


def test_every_committed_config_loads():
    """The cruise TOMLs in configs/ (R2R's keys included) and the example site TOML"""
    from conftest import CONFIGS

    from nav_qa_processing.integrations.openvdm.hook import load_site_config

    for path in sorted(CONFIGS.glob("*_*.toml")):
        assert load_config(path).cruise_id, path
    assert load_site_config(CONFIGS / "site.example.toml").feeds


def test_core_knows_the_keys_r2r_reads():
    """config.INTEGRATION_KEYS, written out as core never imports an integration, against R2R's
    cruise TOML: its ports and device, and a full config written and read back by core
    """
    from nav_qa_processing.config import INTEGRATION_KEYS, fields_of, loads_config
    from nav_qa_processing.integrations.r2r import config as r2r

    assert list(INTEGRATION_KEYS["cruise"]["depart_port"]) == fields_of(r2r.Port)
    assert list(INTEGRATION_KEYS["fileset"]["device"]) == fields_of(r2r.Device)
    full = r2r.CruiseConfig(
        cruise_id="X",
        depart_date=date(2026, 1, 5),
        arrive_date=date(2026, 1, 6),
        fileset_id="1",
        vessel_id="V",
        operator_id="O",
        scheduler_id="S",
        date_issued="2026-01-07",
        date_modified="2026-01-08",
        depart_port=r2r.Port(*"abcd", 1.0, 2.0),
        arrive_port=r2r.Port(*"efgh", 3.0, 4.0),
        fileset_date_created="2026-01-09",
        format_id="100150",
        format_alias="starc_raw",
        device=r2r.Device(*"ijklm"),
        distro_type="post-cruise",
        contact_institution_id="I",
    )
    text = r2r.config_to_toml(full)
    assert loads_config(text).cruise_id == "X"
    assert r2r.loads_cruise_config(text) == full


def test_unknown_site_keys_say_where_they_belong(tmp_path):
    from nav_qa_processing.integrations.openvdm.hook import load_site_config

    path = tmp_path / "site.toml"
    path.write_text('[vessel]\nvessel_name = "Sproul"\n[nav]\nformat = "nav29"\n')
    with pytest.raises(ValueError, match=r"unknown key: \[vessel\] vessel_name: did you mean "):
        load_site_config(path)


@pytest.mark.parametrize(
    "text, message",
    [
        ('cruise = "X"\n', "cruise .top level. must be a table, not a value"),
        ("[cruise]\nextent = 3\n", r"\[cruise\] extent must be a table, not a value"),
        ("[cruise.id]\na = 1\n", r"\[cruise\] id must be a value, not a table"),
        ("thresholds = 1\n", "thresholds .top level. must be a table, not a value"),
        ('[nav]\ndevice = "abc"\n', r"\[nav\] device must be a table, not a value"),
        ("nav = [1, 2]\n", "nav .top level. must be a table, not a value"),
        ('[[cruise]]\nid = "X"\n', r"\[\[cruise\]\] must be one table, \[cruise\]"),
        (
            'cruise = "X"\nfoo = 1\n',
            r"cruise .top level. must be a table, not a value; unknown key: foo .top level.$",
        ),
    ],
)
def test_values_of_the_wrong_shape_are_one_line_errors(text, message):
    """#127: a value where a table belongs, or a table where a value does, is a ValueError
    (one line from the CLI), not a traceback from the loader
    """
    from nav_qa_processing.config import loads_config

    with pytest.raises(ValueError, match=message):
        loads_config(text)


def test_several_nav_feeds_are_an_array_of_tables():
    from nav_qa_processing.config import loads_config

    text = '[[nav]]\nname = "a"\nformat = "nav29"\n[[nav]]\nname = "b"\nformat = "nav33"\n'
    assert loads_config(text, nav="b").nav_format == "nav33"


def test_a_site_toml_value_where_a_table_belongs(tmp_path):
    from nav_qa_processing.integrations.openvdm.hook import load_site_config

    path = tmp_path / "site.toml"
    path.write_text('extent = "everywhere"\n[nav]\nformat = "nav29"\n')
    with pytest.raises(ValueError, match="extent .top level. must be a table, not a value"):
        load_site_config(path)


def test_the_cli_reports_a_badly_shaped_config_in_one_line(tmp_path, capsys):
    from nav_qa_processing.cli import main

    config = tmp_path / "bad.toml"
    config.write_text('cruise = "X"\n[nav]\nformat = "nav33"\n')
    assert main(["run", str(tmp_path), "-c", str(config)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "cruise (top level) must be a table" in err
