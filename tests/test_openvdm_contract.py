"""The OpenVDM hook against OpenVDM's own code, not stand-ins we wrote: how OpenVDM runs the hook
commands INSTALL.md documents, its utils/update_md5_summary.py as the hook calls it, and the
cruise config file it writes for each cruise. Stand-ins for these let --cruiseID and the old
ovdmConfig.json name through. Run with a checkout of OpenVDM's 2.17 branch, with a venv of its
requirements (OPENVDM_SOURCE, default ../openvdm), as CI does; skipped without one.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import ROOT
from test_openvdm import EXTENT, cruise_config

from nav_qa_processing.cli import build_parser
from nav_qa_processing.integrations.openvdm import hook as openvdm

SOURCE = Path(os.environ.get("OPENVDM_SOURCE", ROOT.parent / "openvdm")).absolute()
PYTHON = SOURCE / "venv" / "bin" / "python"
TESTS = Path(__file__).parent

FOUND = (SOURCE / "server/workers/post_hooks.py").exists() and PYTHON.exists()
MISSING = (
    f"no OpenVDM checkout with a venv at {SOURCE}: set OPENVDM_SOURCE to OpenVDM's 2.17 branch"
)
if os.environ.get("OPENVDM_CONTRACT_REQUIRED") and not FOUND:
    raise RuntimeError(MISSING)  # CI's contract job: a skip would pass unnoticed
pytestmark = pytest.mark.skipif(not FOUND, reason=MISSING)

SITE = openvdm.SiteConfig(feeds=[openvdm.SiteFeed(format="nav33", files="raw/POSMV_GGA-*.txt")])


def run_openvdm(action: str, **request) -> dict:
    """tests/openvdm_driver.py's answer, from OpenVDM's code under OpenVDM's Python"""
    completed = subprocess.run(
        [str(PYTHON), str(TESTS / "openvdm_driver.py")],
        input=json.dumps({"action": action, **request}),
        capture_output=True,
        text=True,
        env={**os.environ, "OPENVDM_SOURCE": str(SOURCE)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def api_cruise_config(base_dir: Path) -> dict:
    """getCruiseConfig as OpenVDM's API gives it, with what its export removes"""
    config = cruise_config(base_dir, extent=EXTENT)
    config["warehouseConfig"].update(
        cruiseConfigFn="cruise_config.json",  # OpenVDM's default, CRUISE_CONFIG_FN
        loweringConfigFn="lowering_config.json",
        dataDashboardManifestFn="manifest.json",
        md5SummaryMd5Fn="MD5_Summary.md5",
    )
    config.update(cruiseDataTransfersConfig=[], shipToShoreTransfersConfig=[])
    return config


# The hooks INSTALL.md documents --------------------------------------------------------------


def openvdm_yaml(tmp_path: Path, hooks: dict) -> Path:
    """OpenVDM's openvdm.yaml.dist with the hooks put under its own postHookCommands keys, as
    INSTALL.md says to
    """
    settings = yaml.safe_load((SOURCE / "server/etc/openvdm.yaml.dist").read_text())
    for name in ("postCollectionSystemTransfer", "preFinalizeCurrentCruise"):
        assert name in settings["postHookCommands"], name  # the keys INSTALL.md says it has
        settings["postHookCommands"][name] = hooks[name]
    path = tmp_path / "openvdm.yaml"
    path.write_text(yaml.safe_dump(settings))
    return path


def install_md_hooks() -> dict:
    text = (ROOT / "INSTALL.md").read_text()
    (block,) = [
        b for b in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL) if "postHookCommands" in b
    ]
    return yaml.safe_load(block)["postHookCommands"]


def test_openvdm_runs_the_documented_hooks(tmp_path):
    config = openvdm_yaml(tmp_path, install_md_hooks())
    transfer = {"collectionSystemTransferID": "5", "name": "OpenRVDAS"}
    files = {"new": ["raw/MET-2026-07-09.txt", "raw/POSMV_GGA-2026-07-09.txt"], "updated": []}
    after_transfer = run_openvdm(
        "hook_commands",
        openvdm_yaml=str(config),
        hook="postCollectionSystemTransfer",
        transfer=transfer,
        cruise_id="SP2613",
        files=files,
    )
    (command,) = after_transfer["commandList"]
    assert "missing" not in command
    args = build_parser().parse_args(command["command"][1:])
    assert args.transfer == "OpenRVDAS" and args.min_interval == "6h"
    # OpenVDM joins a file list into one argument, and keeps "--changed-files=" without files
    assert args.changed_files == [" ".join(files["new"]), ""]
    assert openvdm.has_nav_changes(args.changed_files, "raw/POSMV_GGA-*.txt")

    at_finalize = run_openvdm(
        "hook_commands",
        openvdm_yaml=str(config),
        hook="preFinalizeCurrentCruise",
        cruise_id="SP2613",
    )
    (command,) = at_finalize["commandList"]
    assert "missing" not in command  # the transfer is named, not a placeholder
    args = build_parser().parse_args(command["command"][1:])
    assert args.transfer == "OpenRVDAS" and args.changed_files is None and not args.min_interval


def test_openvdm_skips_a_placeholder_without_a_value(tmp_path):
    """Why INSTALL.md names the transfer in preFinalizeCurrentCruise"""
    hooks = install_md_hooks()
    pre = hooks["preFinalizeCurrentCruise"]["commandList"][0]["command"]
    pre[pre.index("OpenRVDAS")] = "{collectionSystemTransferName}"
    at_finalize = run_openvdm(
        "hook_commands",
        openvdm_yaml=str(openvdm_yaml(tmp_path, hooks)),
        hook="preFinalizeCurrentCruise",
        cruise_id="SP2613",
    )
    (command,) = at_finalize["commandList"]
    assert command["missing"] == ["{collectionSystemTransferName}"]


# OpenVDM's update_md5_summary.py, as the hook runs it ---------------------------------------


def openvdm_install(tmp_path: Path) -> Path:
    """An install for queue_md5_update to find: OpenVDM's venv and its utils/"""
    install = tmp_path / "openvdm"
    install.mkdir()
    (install / "venv").symlink_to(SOURCE / "venv")
    (install / "utils").symlink_to(SOURCE / "utils")
    return install


def reports(run: openvdm.HookRun) -> list[Path]:
    run.output_dir.mkdir(parents=True)
    written = [run.output_dir / f"{run.config.stem}_qa_report.pdf"]
    written[0].write_text("")
    return written


def queue(tmp_path, monkeypatch, run, warehouse: Path) -> tuple[str | None, dict | None]:
    """queue_md5_update through OpenVDM's script; what went wrong, and the job it queued"""
    record = tmp_path / "job.json"
    monkeypatch.setenv("PYTHONPATH", str(TESTS / "openvdm_md5"))
    monkeypatch.setenv("OPENVDM_SOURCE", str(SOURCE))
    monkeypatch.setenv("OPENVDM_CONTRACT_RECORD", str(record))
    monkeypatch.setenv("OPENVDM_CONTRACT_WAREHOUSE", str(warehouse))
    problem = openvdm.queue_md5_update(openvdm_install(tmp_path), run, reports(run))
    return problem, json.loads(record.read_text()) if record.exists() else None


def test_openvdms_script_takes_the_hooks_md5_update(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    run = openvdm.resolve(api_cruise_config(warehouse), SITE, "OpenRVDAS")
    problem, job = queue(tmp_path, monkeypatch, run, warehouse)
    assert problem is None
    assert job["data"]["cruiseID"] == "SP2613"
    assert job["data"]["files"]["updated"] == ["Products/NAV_QA/SP2613_qa_report.pdf"]


def test_openvdms_script_refuses_a_cruise_outside_its_warehouse(tmp_path, monkeypatch):
    """A past cruise moved out of OpenVDM's warehouse: OpenVDM refuses its files"""
    moved = tmp_path / "archive" / "SP2613"
    moved.mkdir(parents=True)
    path = moved / "cruise_config.json"
    path.write_text(
        json.dumps({**cruise_config(Path("/x"), extent=EXTENT), "md5SummaryFn": "MD5"})
    )
    run = openvdm.resolve(openvdm.load_cruise_json(path), SITE, "OpenRVDAS")
    problem, job = queue(tmp_path, monkeypatch, run, tmp_path / "warehouse")
    assert "exited 1" in problem and "is not in the cruise directory" in problem
    assert job is None


# The cruise config file OpenVDM writes -------------------------------------------------------


def test_the_hook_reads_the_cruise_config_openvdm_writes(tmp_path):
    """A cruise from the file OpenVDM's export writes resolves as it does from OpenVDM's API"""
    warehouse = tmp_path / "warehouse"
    cruise_dir = warehouse / "SP2613"
    cruise_dir.mkdir(parents=True)
    api = api_cruise_config(warehouse)
    vessel = {"name": "Robert Gordon Sproul", "contact": {"email": "ops@example.org"}}
    exported = run_openvdm(
        "export_cruise_config", cruise_config=api, vessel=vessel, cruise_dir=str(cruise_dir)
    )
    assert exported == {"verdict": True}
    from_file = openvdm.resolve(
        openvdm.load_cruise_json(cruise_dir / "cruise_config.json"), SITE, "OpenRVDAS"
    )
    from_api = openvdm.resolve(
        api, SITE, "OpenRVDAS", vessel=openvdm.vessel_settings(vessel, "openvdm.yaml")
    )
    for name in (
        "cruise_id",
        "cruise_name",
        "cruise_pi",
        "cruise_location",
        "depart_date",
        "arrive_date",
        "extent",
        "manifest_path",
        "vessel_name",
        "contact_email",
    ):
        assert getattr(from_file.config, name) == getattr(from_api.config, name), name
    assert (from_file.fileset_dir, from_file.output_dir) == (
        from_api.fileset_dir,
        from_api.output_dir,
    )
