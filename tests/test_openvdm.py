"""The OpenVDM hook (#49), against a stand-in OpenVDM API and an OpenVDM-style cruise directory
with an OpenRVDAS collection system logging GGA as nav33.
"""

import json
import subprocess
import sys
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import yaml

from nav_qa_processing import maps
from nav_qa_processing.cli import main
from nav_qa_processing.config import Extent, NavDevice, QAConfig, load_config
from nav_qa_processing.fileset import md5sum
from nav_qa_processing.formats.nmea import checksum
from nav_qa_processing.integrations.openvdm import hook as openvdm

EXTENT = {"westernmost": -117.4, "easternmost": -117.2, "southernmost": 32.5, "northernmost": 32.8}
SITE = """[vessel]
name = "Sproul"

[nav]
format = "nav33"
files = "raw/POSMV_GGA-*.txt"

[geocsv]
attribution = "R/V Robert Gordon Sproul, Scripps Institution of Oceanography"
"""


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def cruise_config(base_dir: Path, cruise_id="SP2613", extent: dict | None = None) -> dict:
    """A getCruiseConfig response, trimmed to what the hook reads"""
    return {
        "cruiseID": cruise_id,
        "cruiseExtent": extent,
        "cruiseName": "Sproul engineering cruise",
        "cruisePI": "A. Scientist",
        "cruiseLocation": "San Diego Trough",
        "cruiseStartDate": "2026/07/09 00:00",
        "cruiseEndDate": "2026/07/10 00:00",
        "warehouseConfig": {
            "shipboardDataWarehouseBaseDir": str(base_dir),
            "shipboardDataWarehouseUsername": "survey",
            "md5SummaryFn": "MD5_Summary.txt",
            "cruiseConfigFn": "ovdmConfig.json",
        },
        "collectionSystemTransfersConfig": [
            {"collectionSystemTransferID": "4", "name": "CTD", "destDir": "CTD"},
            {"collectionSystemTransferID": "5", "name": "OpenRVDAS", "destDir": "OpenRVDAS"},
        ],
        "extraDirectoriesConfig": [
            {"name": "Dashboard_Data", "destDir": "OpenVDM/DashboardData", "enable": "1"},
            {"name": "NAV_QA", "destDir": "Products/NAV_QA", "enable": "1"},
        ],
    }


def nav33(**changes) -> openvdm.SiteFeed:
    return openvdm.SiteFeed(format="nav33", **changes)


def site_config(tmp_path, text=SITE) -> Path:
    path = tmp_path / "site.toml"
    path.write_text(text)
    return path


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}"


def openrvdas(directory: Path, minutes: int = 20, outliers: bool = False) -> list[Path]:
    """OpenRVDAS logs of a ship steaming north at 3 m s-1 on 2026-07-09, one GGA a second
    (nav33), and a met station's log beside them. With outliers, a fix at 0, 0 and half a
    minute 40 km west, which QC flags
    """
    raw = directory / "raw"
    raw.mkdir(parents=True)
    start = datetime(2026, 7, 9, 12, tzinfo=UTC)
    lines = []
    for second in range(minutes * 60):
        moment = start + timedelta(seconds=second)
        lat = 32.6 + second * 3 / 111_320
        minutes_lat = (lat - 32) * 60
        position = f"32{minutes_lat:08.5f},N,11718.00000,W"
        if outliers and second == 300:
            position = "0000.00000,N,00000.00000,E"
        elif outliers and 600 <= second < 630:
            position = f"32{minutes_lat:08.5f},N,11743.00000,W"
        body = f"GPGGA,{moment:%H%M%S}.00,{position},2,10,0.9,7.5,M,-35.0,M,,"
        stamp = (moment + timedelta(seconds=0.1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        lines.append(f"{stamp},{nmea(body)}")
    nav = raw / "POSMV_GGA-2026-07-09-120000.txt"
    nav.write_text("\n".join(lines) + "\n")
    (raw / "MET-2026-07-09-120000.txt").write_text("2026-07-09T12:00:00.0Z,WIR,1,2,3\n")
    return [nav]


# Unit behavior ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("2026/07/09 14:30", date(2026, 7, 9)), ("2026/07/09", date(2026, 7, 9)), ("", None)],
)
def test_parse_openvdm_date(text, expected):
    assert openvdm.parse_openvdm_date(text) == expected


@pytest.mark.parametrize(
    "changed, files, expected",
    [
        ([""], "raw/POSMV_GGA-*.txt", False),
        (["raw/MET-2026-07-09-120000.txt"], "raw/POSMV_GGA-*.txt", False),
        (
            ["raw/MET-2026-07-09.txt raw/POSMV_GGA-2026-07-09-120000.txt"],
            "raw/POSMV_GGA-*.txt",
            True,
        ),
        (["OpenRVDAS/raw/POSMV_GGA-2026-07-09.txt"], "raw/POSMV_GGA-*.txt", True),  # a prefix
        (["raw/sub dir/POSMV_GGA-x.txt"], "raw/POSMV_GGA-*.txt", False),  # * stops at /
        (["raw/a/b/gnss_1.txt"], "**/gnss_?.txt", True),
        (["anything.txt"], "", True),  # no glob: any file
        ([""], "", False),
    ],
)
def test_has_nav_changes(changed, files, expected):
    assert openvdm.has_nav_changes(changed, files) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ({}, None),
        (EXTENT, Extent(**EXTENT)),
        ({"westernmost": "x"}, None),  # malformed: ignored
    ],
)
def test_openvdm_extent(value, expected):
    assert openvdm.openvdm_extent(value) == expected


def test_resolve_paths_and_metadata(tmp_path):
    site = openvdm.load_site_config(site_config(tmp_path))
    run = openvdm.resolve(cruise_config(tmp_path, extent=EXTENT), site, "OpenRVDAS")
    assert run.fileset_dir == tmp_path / "SP2613" / "OpenRVDAS"
    assert run.output_dir == tmp_path / "SP2613" / "Products" / "NAV_QA"
    assert run.owner == "survey"
    config = run.config
    assert (config.cruise_id, config.cruise_pi, config.vessel_name) == (
        "SP2613",
        "A. Scientist",
        "Sproul",
    )
    assert (config.nav_format, config.nav_files) == ("nav33", "raw/POSMV_GGA-*.txt")
    assert config.attribution.startswith("R/V Robert Gordon Sproul")
    assert (config.depart_date, config.arrive_date) == (date(2026, 7, 9), date(2026, 7, 10))
    assert config.extent == Extent(**EXTENT) and run.extent_source == "openvdm"
    assert config.manifest_path == tmp_path / "SP2613" / "MD5_Summary.txt"
    assert run.notes == []


def test_resolve_extent_fallbacks(tmp_path):
    site = openvdm.SiteConfig(feeds=[nav33()], extent=Extent(-120, -110, 30, 35))
    run = openvdm.resolve(cruise_config(tmp_path), site, "OpenRVDAS")
    assert run.extent_source == "site config" and run.config.extent == site.extent
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "OpenRVDAS")
    assert run.extent_source == "none" and run.config.extent is None
    assert any("area check is n/a" in note for note in run.notes)


def test_resolve_notes_what_the_site_config_lacks(tmp_path):
    run = openvdm.resolve(
        cruise_config(tmp_path, extent=EXTENT), openvdm.SiteConfig(), "OpenRVDAS"
    )
    notes = " ".join(run.notes)
    assert "no [nav] format, so the QA can't run" in notes
    assert "no [nav] files: every file in OpenRVDAS is read" in notes


def test_resolve_explains_missing_configuration(tmp_path):
    with pytest.raises(openvdm.OpenVDMError, match="no collection system transfer named 'XBT'"):
        openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "XBT")
    with pytest.raises(openvdm.OpenVDMError, match="no extra directory named 'GPS_QA'"):
        site = openvdm.SiteConfig(output_extra_directory="GPS_QA")
        openvdm.resolve(cruise_config(tmp_path), site, "OpenRVDAS")
    with pytest.raises(openvdm.OpenVDMError, match="no current cruise"):
        openvdm.resolve(cruise_config(tmp_path, cruise_id=""), openvdm.SiteConfig(), "OpenRVDAS")


def test_resolve_without_a_start_date(tmp_path):
    response = cruise_config(tmp_path) | {"cruiseStartDate": ""}
    run = openvdm.resolve(response, openvdm.SiteConfig(), "OpenRVDAS")
    assert run.config.depart_date is None and run.config.arrive_date is None
    assert any("no start date" in note for note in run.notes)


def test_open_ended_cruise_ends_today_in_utc(tmp_path, monkeypatch):
    assert openvdm.utc_today() == datetime.now(UTC).date()
    monkeypatch.setattr(openvdm, "utc_today", lambda: date(2026, 10, 14))
    response = cruise_config(tmp_path) | {"cruiseEndDate": ""}
    run = openvdm.resolve(response, openvdm.SiteConfig(), "OpenRVDAS")
    assert run.config.arrive_date == date(2026, 10, 14)
    assert "arrive_date is today, UTC (2026-10-14)" in " ".join(run.notes)


@pytest.mark.parametrize(
    "block, expected",
    [
        (None, {}),
        (
            {"name": "Sproul", "contact": {"email": "ops@example.org", "institution": ""}},
            {
                "vessel_name": "Sproul",
                "contact_email": "ops@example.org",
            },
        ),
        ({"name": "  ", "contact": "not a table"}, {}),
    ],
)
def test_vessel_settings(block, expected):
    assert openvdm.vessel_settings(block, "openvdm.yaml").settings == expected


def test_resolve_prefers_the_site_configs_vessel_settings(tmp_path):
    """The site TOML's vessel settings win; openvdm.yaml's fill those it leaves blank (#112)"""
    site = openvdm.SiteConfig(vessel_name="R/V Sproul", contact_email="site@example.org")
    vessel = openvdm.vessel_settings(
        {"name": "Robert Gordon Sproul", "contact": {"institution": "SIO"}}, "openvdm.yaml"
    )
    run = openvdm.resolve(cruise_config(tmp_path), site, "OpenRVDAS", vessel)
    config = run.config
    assert (config.vessel_name, config.contact_institution, config.contact_email) == (
        "R/V Sproul",  # the site config's
        "SIO",  # blank there: openvdm.yaml's
        "site@example.org",
    )
    assert run.vessel_source == "openvdm.yaml"
    assert (
        "vessel_name is 'R/V Sproul' in the site config and 'Robert Gordon Sproul' in OpenVDM's "
        "openvdm.yaml; using the site config's"
    ) in " ".join(run.notes)


def test_resolve_reads_the_vessel_from_ovdmconfig_json(tmp_path, caplog):
    cruise_dir = tmp_path / "SP2613"
    cruise_dir.mkdir()
    (cruise_dir / "ovdmConfig.json").write_text(json.dumps({"vessel": {"name": "Sproul"}}))
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "OpenRVDAS")
    assert (run.vessel_source, run.config.vessel_name) == ("ovdmConfig.json", "Sproul")
    (cruise_dir / "ovdmConfig.json").write_text("{not json")
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "OpenRVDAS")
    assert run.vessel_source == "none" and "can't read the vessel settings" in caplog.text


def test_site_config_round_trips(tmp_path):
    site = openvdm.load_site_config(site_config(tmp_path, SITE + '\n[theme]\npage_size = "A4"\n'))
    text = openvdm.site_config_to_toml(site)
    assert openvdm.load_site_config(site_config(tmp_path, text)) == site
    blank = openvdm.site_config_to_toml(openvdm.SiteConfig())
    assert '# format = ""' in blank and 'output_extra_directory = "NAV_QA"' in blank
    assert openvdm.load_site_config(site_config(tmp_path, blank)) == openvdm.SiteConfig()


def test_resolve_passes_the_site_device_to_the_snapshot(tmp_path):
    device = NavDevice(make="Applanix", model="POS MV", location="main lab")
    site = openvdm.SiteConfig(feeds=[nav33(device=device)])
    run = openvdm.resolve(cruise_config(tmp_path), site, "OpenRVDAS")
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(openvdm.cruise_toml(run, "http://openvdm/"))
    assert load_config(snapshot).nav_device == device


def test_resolve_passes_the_site_theme_to_the_snapshot(tmp_path):
    site = openvdm.SiteConfig(feeds=[nav33()], theme={"page_size": "A4"})
    run = openvdm.resolve(cruise_config(tmp_path), site, "OpenRVDAS")
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(openvdm.cruise_toml(run, "http://openvdm/"))
    loaded = load_config(snapshot)
    assert loaded.theme == {"page_size": "A4"} and loaded.nav_format == "nav33"


# Adding the reports to OpenVDM's MD5 summary ---------------------------------------------------


def openvdm_install(root: Path) -> Path:
    """A stand-in OpenVDM install with the MD5 summary script, as from OpenVDM 2.16.1"""
    for path in ("venv/bin/python", "utils/update_md5_summary.py", "server/etc/openvdm.yaml"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    return root


class FakeRun:
    """subprocess.run standing in for OpenVDM's script; records the commands"""

    def __init__(self, returncode=0, stderr="", error=None):
        self.commands, self.returncode, self.stderr, self.error = [], returncode, stderr, error

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


def written_reports(tmp_path):
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "OpenRVDAS")
    plots = run.output_dir / "SP2613_plots"
    plots.mkdir(parents=True)
    (plots / "track_map.svg").write_text("<svg/>")
    pdf = run.output_dir / "SP2613_qa_report.pdf"
    pdf.write_text("%PDF")
    return run, [pdf, plots]


def test_openvdm_install_dir():
    assert (
        openvdm.openvdm_install_dir("/srv/openvdm/server/etc/openvdm.yaml")
        == Path("/srv/openvdm").absolute()
    )


def test_queue_md5_update(tmp_path, monkeypatch):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_reports(tmp_path)
    fake = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert openvdm.queue_md5_update(install, run, written) is None
    (command,) = fake.commands
    assert command[:4] == [
        str(install / "venv/bin/python"),
        str(install / "utils/update_md5_summary.py"),
        "--cruiseID",
        "SP2613",
    ]
    assert command[4:] == [
        "Products/NAV_QA/SP2613_plots/track_map.svg",
        "Products/NAV_QA/SP2613_qa_report.pdf",
    ]


@pytest.mark.parametrize(
    "fake, expected",
    [
        (FakeRun(1, "Traceback ...\nNo Gearman server"), "exited 1: No Gearman server"),
        (FakeRun(error=subprocess.TimeoutExpired("python", 120)), "couldn't run"),
    ],
)
def test_queue_md5_update_failures(tmp_path, monkeypatch, fake, expected):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_reports(tmp_path)
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert expected in openvdm.queue_md5_update(install, run, written)


def test_queue_md5_update_without_the_script(tmp_path):
    run, written = written_reports(tmp_path)
    assert "needs OpenVDM 2.16.1" in openvdm.queue_md5_update(tmp_path / "none", run, written)


# The hook end to end ---------------------------------------------------------------------------


@pytest.fixture
def api_response(tmp_path):
    """The getCruiseConfig response openvdm_api serves; tests can change it"""
    return cruise_config(tmp_path / "warehouse", extent=EXTENT)


@pytest.fixture
def api_state():
    """The stand-in API's current cruise, its answer to setCruiseExtent, and what was posted
    to it: (X-Worker-Token, extent)
    """
    return {"cruise": "SP2613", "status": 200, "posts": []}


@pytest.fixture
def openvdm_api(tmp_path, api_response, api_state):
    """A stand-in OpenVDM web API serving getCruiseConfig for a cruise under tmp_path, and
    getCruiseID and setCruiseExtent (OpenVDM 2.17)
    """
    response = api_response

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/api/warehouse/getCruiseConfig"):
                self.reply(200, response)
            elif self.path.endswith("/api/warehouse/getCruiseID"):
                self.reply(200, {"cruiseID": api_state["cruise"]})
            else:
                self.send_error(404)

        def do_POST(self):
            if not self.path.endswith("/api/warehouse/setCruiseExtent"):
                self.send_error(404)
                return
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            extent = json.loads(form["extent"][0])
            api_state["posts"].append((self.headers.get("X-Worker-Token"), extent))
            self.reply(api_state["status"], {"cruiseExtent": extent})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", tmp_path / "warehouse" / "SP2613"
    server.shutdown()


def test_hook_writes_reports_into_the_extra_directory(
    openvdm_api, tmp_path, monkeypatch, caplog, capsys
):
    site_root, cruise_dir = openvdm_api
    site = str(site_config(tmp_path))
    install = openvdm_install(tmp_path / "openvdm")
    monkeypatch.setattr(
        "nav_qa_processing.integrations.openvdm.cli.DEFAULT_OPENVDM_CONFIG",
        install / "server/etc/openvdm.yaml",
    )
    md5_update = FakeRun(1, "Unable to queue the MD5 summary update: no Gearman")
    monkeypatch.setattr(openvdm.subprocess, "run", md5_update)
    hook = ["openvdm", "OpenRVDAS", "--site-root", site_root, "--site-config", site]

    # Before the first transfer there's nothing to assess
    assert main(hook) == 0
    assert "doesn't exist yet" in capsys.readouterr().out
    (nav,) = openrvdas(cruise_dir / "OpenRVDAS")
    summary = cruise_dir / "MD5_Summary.txt"
    summary.write_text(f"{md5sum(nav)} OpenRVDAS/raw/{nav.name}\n")

    # A transfer without nav files is skipped
    assert main([*hook, "--changed-files=raw/MET-2026-07-09-120000.txt"]) == 0
    assert "no new or updated nav files" in capsys.readouterr().out
    assert not (cruise_dir / "Products").exists()

    assert main([*hook, f"--changed-files=raw/{nav.name}"]) == 0
    out = capsys.readouterr().out
    assert "1200 fixes (nav33) from 1 file in" in out  # the met log isn't read
    assert "Within the expected area: 0 outside" in out  # OpenVDM's cruiseExtent
    output = cruise_dir / "Products/NAV_QA"
    assert {p.name for p in output.iterdir()} == {
        "SP2613_config.toml",
        "SP2613_bestres.geoCSV",
        "SP2613_1min.geoCSV",
        "SP2613_control.geoCSV",
        "SP2613_plots",
        "SP2613_qa_report.pdf",
        "SP2613_qa.ipynb",
    }
    snapshot = load_config(output / "SP2613_config.toml")
    assert type(snapshot) is QAConfig and snapshot.nav_format == "nav33"
    assert "Robert Gordon Sproul" in (output / "SP2613_bestres.geoCSV").read_text()
    # The notebook re-runs from the snapshot of what OpenVDM supplied, without R2R
    notebook = (output / "SP2613_qa.ipynb").read_text()
    assert "SP2613_config.toml" in notebook and "r2r" not in notebook

    # Every file written goes to OpenVDM's MD5 summary; a failure to queue doesn't fail the hook
    (command,) = md5_update.commands
    assert command[2:4] == ["--cruiseID", "SP2613"]
    queued = set(command[4:])
    assert {"Products/NAV_QA/SP2613_config.toml", "Products/NAV_QA/SP2613_qa.ipynb"} < queued
    assert len([f for f in queued if f.endswith(".svg")]) == 4
    assert "the reports aren't in OpenVDM's MD5 summary" in caplog.text

    assert "checksum" not in out  # the nav file matches OpenVDM's MD5 summary

    # The next transfer replaces the reports, without --force; the MD5 summary is checked
    summary.write_text(f"{'0' * 32} OpenRVDAS/raw/{nav.name}\n")
    assert main(hook) == 0
    assert f"raw/{nav.name}: checksum doesn't match the manifest" in capsys.readouterr().out


def test_hook_reports_failures_to_openvdm_in_one_line(openvdm_api, tmp_path, capsys):
    site_root, cruise_dir = openvdm_api
    assert main(["openvdm", "XBT", "--site-root", site_root]) == 1
    err = capsys.readouterr().err
    assert err == "nav-qa-processing: OpenVDMError: no collection system transfer named 'XBT'\n"
    openrvdas(cruise_dir / "OpenRVDAS")
    assert main(["openvdm", "OpenRVDAS", "--site-root", site_root]) == 1  # no site config
    assert "no nav format: set [nav] format in the site config" in capsys.readouterr().err


# config-from-openvdm ---------------------------------------------------------------------------


def test_config_from_openvdm_writes_the_config(openvdm_api, tmp_path, capsys):
    site_root, _ = openvdm_api
    args = ["config-from-openvdm", "OpenRVDAS", "--site-root", site_root, "-o", str(tmp_path)]
    assert main(args) == 0
    err = capsys.readouterr().err
    assert "SP2613: cruise extent from openvdm, vessel settings from none" in err
    assert "no [nav] format" in err  # no site config
    text = (tmp_path / "SP2613.toml").read_text()
    assert text.startswith(f"# Resolved from OpenVDM ({site_root}) for OpenRVDAS")
    assert load_config(tmp_path / "SP2613.toml").extent == Extent(**EXTENT)
    assert main(args) == 1 and "use --force" in capsys.readouterr().err


def test_config_from_openvdm_with_openvdm_yaml(openvdm_api, tmp_path, capsys):
    site_root, _ = openvdm_api
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text(
        yaml.safe_dump({"siteRoot": site_root.rstrip("/"), "vessel": {"name": "Sproul"}})
    )
    site = str(site_config(tmp_path))
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(openvdm_yaml)]
    assert main([*args, "--site-config", site, "-o", "-"]) == 0
    captured = capsys.readouterr()
    config = load_config_text(captured.out)
    assert (config.vessel_name, config.nav_format) == ("Sproul", "nav33")
    assert "vessel settings from openvdm.yaml" in captured.err


def load_config_text(text: str) -> QAConfig:
    from nav_qa_processing.config import loads_config

    return loads_config(text)


def test_openvdm_yaml_not_found(tmp_path, capsys):
    missing = tmp_path / "openvdm.yaml"
    assert main(["openvdm", "OpenRVDAS", "--openvdm-config", str(missing)]) == 1
    err = capsys.readouterr().err
    assert f"no openvdm.yaml at {missing}" in err and "--site-root" in err
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(missing), "-o", "-"]
    assert main(args) == 1
    assert "give its path with --openvdm-config" in capsys.readouterr().err


def test_without_pyyaml(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml raises ImportError
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text('siteRoot: "http://127.0.0.1/"\n')
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(openvdm_yaml), "-o", "-"]
    assert main(args) == 1
    assert "uv sync --extra openvdm" in capsys.readouterr().err


def test_a_cruise_table_in_the_site_config_is_explained(tmp_path):
    """The site TOML describes the ship: each cruise comes from OpenVDM"""
    path = site_config(tmp_path, '[cruise]\npi = "A. Scientist"\n\n[nav]\nformat = "nav33"\n')
    with pytest.raises(
        ValueError, match=r"cruise \(top level\): a site TOML has no cruise.*OpenVDM"
    ):
        openvdm.load_site_config(path)


def test_the_example_site_config_loads():
    site = openvdm.load_site_config(Path(__file__).parents[1] / "configs/site.example.toml")
    assert (site.primary.format, site.primary.files) == ("nav33", "raw/POSMV_GGA-*.txt")
    assert site.output_extra_directory == "NAV_QA" and site.extent is None


# Setting OpenVDM's cruiseExtent (#1) -----------------------------------------------------------


def openvdm_yaml(tmp_path, site_root: str, key: str | None = "worker-secret") -> Path:
    """openvdm.yaml for a stand-in OpenVDM install at tmp_path/openvdm"""
    path = openvdm_install(tmp_path / "openvdm") / "server/etc/openvdm.yaml"
    settings = {"siteRoot": site_root, "vessel": {"name": "Sproul"}}
    if key is not None:
        settings["workerApiKey"] = key
    path.write_text(yaml.safe_dump(settings))
    return path


@pytest.fixture
def hook_with_key(openvdm_api, tmp_path, monkeypatch):
    """The hook's arguments with an openvdm.yaml that has a worker key, over a cruise whose
    track has a fix at 0, 0 and half a minute 40 km off it
    """
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun())
    site_root, cruise_dir = openvdm_api
    openrvdas(cruise_dir / "OpenRVDAS", outliers=True)
    config = openvdm_yaml(tmp_path, site_root)
    site = str(site_config(tmp_path))
    return ["openvdm", "OpenRVDAS", "--openvdm-config", str(config), "--site-config", site]


def test_hook_sets_the_cruise_extent_from_the_good_fixes(hook_with_key, api_state, capsys):
    assert main(hook_with_key) == 0
    out = capsys.readouterr().out
    assert "cruise extent from the fixes" in out
    assert "Within the expected area: no extent configured" in out  # the extent is the track's own
    ((token, extent),) = api_state["posts"]
    assert token == "worker-secret"
    # The fix at 0, 0 and the stretch 40 km west are left out
    assert extent["westernmost"] == extent["easternmost"] == -117.3
    assert extent["southernmost"] == pytest.approx(32.6, abs=1e-5)
    assert extent["northernmost"] == pytest.approx(32.6 + 1199 * 3 / 111_320, abs=1e-5)
    assert extent == {edge: round(value, 6) for edge, value in extent.items()}
    assert "set OpenVDM's cruise extent to 32.6" in out


def test_a_refused_extent_is_a_warning(hook_with_key, api_state, caplog, capsys):
    api_state["status"] = 403
    assert main(hook_with_key) == 0  # the QA succeeded
    assert "workerApiKey in openvdm.yaml must match WORKER_API_KEY" in caplog.text
    assert "set OpenVDM's cruise extent" not in capsys.readouterr().out


def test_the_extent_isnt_set_for_another_cruise(hook_with_key, api_state, caplog):
    api_state["cruise"] = "SP2614"  # OpenVDM moved on during the run
    assert main(hook_with_key) == 0
    assert api_state["posts"] == []
    assert "OpenVDM's current cruise is now 'SP2614', not 'SP2613'" in caplog.text


def test_unreachable_openvdm_when_setting_the_extent():
    problem = openvdm.set_cruise_extent(
        "http://127.0.0.1:9/", "SP2613", Extent(-117.3, -117.3, 32.6, 32.7), "key"
    )
    assert problem.startswith("couldn't ask OpenVDM for its current cruise")


def test_no_set_extent_checks_against_openvdms_extent(hook_with_key, api_state, capsys):
    assert main([*hook_with_key, "--no-set-extent"]) == 0
    out = capsys.readouterr().out
    assert (
        "cruise extent from openvdm" in out
        and "Within the expected area: no extent configured" not in out
    )
    assert api_state["posts"] == []


@pytest.mark.parametrize(
    "how, expected",
    [
        ("site root", "with --site-root there's no openvdm.yaml workerApiKey"),
        ("no key", "openvdm.yaml has no workerApiKey (OpenVDM 2.17)"),
        ("OpenVDM 2.16", "OpenVDM has no cruiseExtent (it needs OpenVDM 2.17)"),
    ],
)
def test_without_a_way_to_set_the_extent_it_is_checked(
    openvdm_api, api_response, api_state, tmp_path, monkeypatch, capsys, how, expected
):
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun())
    site_root, cruise_dir = openvdm_api
    openrvdas(cruise_dir / "OpenRVDAS")
    hook = ["openvdm", "OpenRVDAS", "--site-config", str(site_config(tmp_path))]
    if how == "site root":
        hook += ["--site-root", site_root]
    else:
        key = None if how == "no key" else "worker-secret"
        hook += ["--openvdm-config", str(openvdm_yaml(tmp_path, site_root, key))]
    if how == "OpenVDM 2.16":
        del api_response["cruiseExtent"]  # getCruiseConfig before OpenVDM 2.17
    assert main(hook) == 0
    out = capsys.readouterr().out
    assert f"note: {expected}" in out
    assert api_state["posts"] == []
    if how != "OpenVDM 2.16":
        assert "Within the expected area: 0 outside" in out  # OpenVDM's cruiseExtent


# Several position sensors (#58) ----------------------------------------------------------------

FEEDS = """[[nav]]
name = "posmv"
primary = true
transfer = "OpenRVDAS"
format = "nav33"
files = "raw/POSMV_GGA-*.txt"

[[nav]]
name = "seapath"
transfer = "OpenRVDAS"
format = "nav33"
files = "raw/SEAPATH_GGA-*.txt"
[nav.device]
model = "Seapath 380"

[[nav]]
name = "ctdgps"
transfer = "CTD"
format = "nav33"
files = "gps/*.txt"
"""


@pytest.fixture
def two_feeds(openvdm_api, tmp_path, monkeypatch):
    """The hook's arguments, with a key, over a cruise logging a POS MV and a Seapath"""
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun())
    site_root, cruise_dir = openvdm_api
    (posmv,) = openrvdas(cruise_dir / "OpenRVDAS")
    seapath = posmv.with_name(posmv.name.replace("POSMV", "SEAPATH"))
    seapath.write_text(posmv.read_text())
    config = openvdm_yaml(tmp_path, site_root)
    site = str(site_config(tmp_path, FEEDS))
    hook = ["openvdm", "OpenRVDAS", "--openvdm-config", str(config), "--site-config", site]
    return hook, cruise_dir / "Products/NAV_QA", seapath


def test_feeds_load_with_their_transfers(tmp_path):
    site = openvdm.load_site_config(site_config(tmp_path, FEEDS))
    assert [(f.name, f.primary, f.transfer) for f in site.feeds] == [
        ("posmv", True, "OpenRVDAS"),
        ("seapath", False, "OpenRVDAS"),
        ("ctdgps", False, "CTD"),
    ]
    assert [f.name for f in site.feeds_for("OpenRVDAS")] == ["posmv", "seapath"]
    assert [f.name for f in site.feeds_for("XBT")] == []
    text = openvdm.site_config_to_toml(site)
    assert text.count("\n[[nav]]\n") == 3
    assert openvdm.load_site_config(site_config(tmp_path, text)) == site


def test_hook_assesses_each_feed_of_its_transfer(two_feeds, api_state, capsys):
    hook, output, _ = two_feeds
    assert main(hook) == 0
    out = capsys.readouterr().out
    names = {p.name for p in output.iterdir()}
    assert {"SP2613_bestres.geoCSV", "SP2613_config.toml", "SP2613_qa_report.pdf"} <= names
    assert {"SP2613_seapath_bestres.geoCSV", "SP2613_seapath_config.toml"} <= names
    assert not any("ctdgps" in name for name in names)  # another transfer's feed
    # Only the primary sets cruiseExtent; the Seapath is checked against it
    assert len(api_state["posts"]) == 1
    assert "SP2613_seapath: " in out and "cruise extent from the primary feed" in out
    assert out.count("Within the expected area: 0 outside") == 1
    snapshot = load_config(output / "SP2613_seapath_config.toml")
    assert (snapshot.stem, snapshot.nav_primary) == ("SP2613_seapath", False)
    assert snapshot.nav_device == NavDevice(model="Seapath 380")


def test_hook_runs_only_the_feeds_with_changed_files(two_feeds, api_state):
    hook, output, seapath = two_feeds
    assert main([*hook, f"--changed-files=raw/{seapath.name}"]) == 0
    names = {p.name for p in output.iterdir()}
    assert "SP2613_seapath_bestres.geoCSV" in names and "SP2613_bestres.geoCSV" not in names
    assert api_state["posts"] == []  # the primary didn't run


def test_a_failing_feed_does_not_stop_the_others(two_feeds, tmp_path, api_state, capsys):
    hook, output, _ = two_feeds
    broken = FEEDS.replace(
        'name = "seapath"\ntransfer = "OpenRVDAS"\nformat = "nav33"',
        'name = "seapath"\ntransfer = "OpenRVDAS"\nformat = "nav99"',
    )
    site_config(tmp_path, broken)
    assert main(hook) == 1
    err = capsys.readouterr().err
    assert err.startswith("nav-qa-processing: OpenVDMError: 1 of 2 feeds failed: seapath: ")
    assert err.count("\n") == 1
    assert "SP2613_bestres.geoCSV" in {p.name for p in output.iterdir()}
    assert len(api_state["posts"]) == 1  # the primary still set the extent


def test_config_from_openvdm_writes_each_feed(two_feeds, tmp_path, capsys):
    hook, _, _ = two_feeds
    args = ["config-from-openvdm", *hook[1:]]
    out = tmp_path / "configs"
    assert main([*args, "-o", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {"SP2613.toml", "SP2613_seapath.toml"}
    assert load_config(out / "SP2613_seapath.toml").stem == "SP2613_seapath"
    assert main([*args, "-o", "-"]) == 1
    assert "several feeds (posmv, seapath): give -o OUTPUT_DIR" in capsys.readouterr().err
