"""Several position sensors in one config ([[nav]], #58): loading, naming and `run`."""

from datetime import UTC, datetime, timedelta

import pytest

from nav_qa_processing import maps
from nav_qa_processing.cli import main
from nav_qa_processing.config import (
    NavDevice,
    QAConfig,
    config_to_toml,
    load_config,
    load_configs,
    loads_config,
)
from nav_qa_processing.formats.nmea import checksum

FEEDS = """\
[cruise]
id = "SP2613"

[[nav]]
name = "seapath"
format = "nav33"
files = "SEAPATH_GGA-*.txt"

[[nav]]
name = "posmv"
primary = true
format = "nav33"
files = "POSMV_GGA-*.txt"
[nav.device]
model = "POS MV"
"""


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def write(tmp_path, text: str, name="feeds.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def sensor_log(directory, prefix: str, talker: str, minutes: int = 5, offset: float = 0.0):
    """An OpenRVDAS-style log (nav33) of a ship steaming north at 3 m s-1, one GGA a second"""
    directory.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 7, 9, 12, tzinfo=UTC)
    lines = []
    for second in range(minutes * 60):
        moment = start + timedelta(seconds=second)
        lat_minutes = (0.6 + second * 3 / 111_320 + offset) * 60
        body = (
            f"{talker}GGA,{moment:%H%M%S}.00,32{lat_minutes:08.5f},N,11718.00000,W,2,10,0.9,"
            "7.5,M,-35.0,M,,"
        )
        stamp = (moment + timedelta(seconds=0.1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        lines.append(f"{stamp},${body}*{checksum(body):02X}")
    path = directory / f"{prefix}_GGA-2026-07-09-120000.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


# Loading ------------------------------------------------------------------------------------


def test_feeds_load_primary_first(tmp_path):
    path = write(tmp_path, FEEDS)
    posmv, seapath = load_configs(path)
    assert (posmv.nav_name, posmv.nav_primary, posmv.stem) == ("posmv", True, "SP2613")
    assert (seapath.nav_name, seapath.nav_primary) == ("seapath", False)
    assert seapath.stem == seapath.identifier == "SP2613_seapath"
    assert posmv.nav_device == NavDevice(model="POS MV") and not seapath.nav_device
    assert load_config(path) == posmv  # the primary by default
    assert load_config(path, nav="seapath") == seapath
    with pytest.raises(ValueError, match="no \\[\\[nav\\]\\] named 'gps'; its feeds: posmv, sea"):
        load_config(path, nav="gps")


def test_without_a_primary_the_first_feed_is(tmp_path):
    first, second = load_configs(write(tmp_path, FEEDS.replace("primary = true\n", "")))
    assert (first.nav_name, first.nav_primary, second.nav_primary) == ("seapath", True, False)


@pytest.mark.parametrize(
    "change, message",
    [
        (('name = "seapath"\n', ""), "each \\[\\[nav\\]\\] needs a name"),
        (('name = "seapath"', 'name = "posmv"'), "names repeated: posmv"),
        (
            ('format = "nav33"\nfiles = "SEA', 'primary = true\nformat = "nav33"\nfiles = "SEA'),
            "more than one primary \\[\\[nav\\]\\]: seapath, posmv",
        ),
        (('name = "seapath"', 'name = "sea path"'), "use letters, digits, _ and -"),
    ],
)
def test_bad_feeds_are_errors(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_configs(write(tmp_path, FEEDS.replace(*change, 1)))


def test_a_single_nav_table_is_the_primary(tmp_path):
    (config,) = load_configs(write(tmp_path, '[nav]\nformat = "nav29"\n'))
    assert config.nav_primary and config.nav_name == ""
    with pytest.raises(ValueError, match="primary = false needs a name"):
        load_configs(write(tmp_path, '[nav]\nprimary = false\nformat = "nav29"\n'))


def test_a_feed_round_trips_as_its_own_config(tmp_path):
    """A non-primary feed's snapshot keeps its name, so its re-run keeps its file names"""
    seapath = load_config(write(tmp_path, FEEDS), nav="seapath")
    text = config_to_toml(seapath)
    assert 'name = "seapath"' in text and "primary = false" in text
    again = loads_config(text)
    assert again.stem == "SP2613_seapath" and again.nav_files == "SEAPATH_GGA-*.txt"
    assert QAConfig(cruise_id="X", nav_name="gps").stem == "X"  # primary: the cruise alone


# run ----------------------------------------------------------------------------------------


def test_run_assesses_every_feed(tmp_path, capsys):
    data = tmp_path / "fileset"
    sensor_log(data, "POSMV", "IN")
    sensor_log(data, "SEAPATH", "GP", offset=0.00001)
    config = write(tmp_path, FEEDS)
    out = tmp_path / "out"
    assert main(["run", str(data), "-c", str(config), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "SP2613: 300 fixes (nav33)" in printed and "SP2613_seapath: 300 fixes" in printed
    assert "receiver: POS MV; talker IDs: IN" in printed
    names = {p.name for p in out.iterdir()}
    assert {"SP2613_bestres.geoCSV", "SP2613_qa_report.pdf", "SP2613_qa.ipynb"} <= names
    assert {"SP2613_seapath_bestres.geoCSV", "SP2613_seapath_plots"} <= names
    # Each notebook re-runs its own feed
    notebook = (out / "SP2613_seapath_qa.ipynb").read_text()
    assert "load_config(CONFIG, nav='seapath')" in notebook
    assert "load_config(CONFIG, nav='posmv')" in (out / "SP2613_qa.ipynb").read_text()
    # Both feeds' outputs are checked before anything is written
    assert main(["run", str(data), "-c", str(config), "-o", str(out)]) == 1
    assert "use --force" in capsys.readouterr().err


def test_run_one_feed(tmp_path, capsys):
    data = tmp_path / "fileset"
    sensor_log(data, "SEAPATH", "GP")
    out = tmp_path / "out"
    args = ["run", str(data), "-c", str(write(tmp_path, FEEDS)), "-o", str(out)]
    assert main([*args, "--nav", "seapath"]) == 0
    assert {p.name for p in out.iterdir() if p.suffix == ".geoCSV"} == {
        "SP2613_seapath_bestres.geoCSV",
        "SP2613_seapath_1min.geoCSV",
        "SP2613_seapath_control.geoCSV",
    }
    capsys.readouterr()
    assert main([*args, "--nav", "gps"]) == 1
    assert "no [[nav]] named 'gps'" in capsys.readouterr().err
    assert main([*args, "-f", "nav29"]) == 1
    assert "-f applies to one feed: pick one of posmv, seapath with --nav" in (
        capsys.readouterr().err
    )
    assert main(["run", str(data), "--nav", "seapath"]) == 1
    assert "give -c CONFIG_TOML" in capsys.readouterr().err
