"""site-config: the per-ship TOML for the OpenVDM hook, from prompts."""

import io

import pytest

from nav_qa_processing.cli import main
from nav_qa_processing.config import Extent, NavDevice
from nav_qa_processing.integrations.openvdm.hook import (
    SiteConfig,
    load_site_config,
    site_config_to_toml,
)
from nav_qa_processing.integrations.openvdm.site_setup import Prompter, prompt_site_config


@pytest.fixture(autouse=True)
def no_openvdm_install(monkeypatch, tmp_path):
    """site-config reads /opt/openvdm's openvdm.yaml when it exists; not in these tests"""
    monkeypatch.setattr("nav_qa_processing.cli.DEFAULT_OPENVDM_CONFIG", tmp_path / "missing.yaml")


def answer(monkeypatch, *lines: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(f"{line}\n" for line in lines)))


def test_a_new_site_toml(tmp_path, monkeypatch, capsys):
    answer(
        monkeypatch,
        "Roger Revelle",
        "Scripps Institution of Oceanography",
        "not an email",  # re-asked
        "tech@example.org",
        "",  # nav format: required, re-asked
        "nav99",  # no reader, re-asked
        "nav33",
        "raw/POSMV_GGA-*.txt",
        "Applanix",  # the device: make, model, serial number, location
        "POS MV",
        "",
        "main lab",
        "R/V Roger Revelle, Scripps Institution of Oceanography",
        "",  # reports extra directory: default
        "-125",  # westernmost
        "-115",  # easternmost
        "36",  # southernmost
        "30",  # northernmost, south of southernmost: the box is re-asked
        "-125",
        "-115",
        "30",
        "91",  # outside ±90, re-asked
        "36",
    )
    output = tmp_path / "configs" / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    site = load_site_config(output)
    assert site == SiteConfig(
        vessel_name="Roger Revelle",
        nav_format="nav33",
        nav_files="raw/POSMV_GGA-*.txt",
        nav_device=NavDevice(make="Applanix", model="POS MV", location="main lab"),
        extent=Extent(-125.0, -115.0, 30.0, 36.0),
        contact_institution="Scripps Institution of Oceanography",
        contact_email="tech@example.org",
        attribution="R/V Roger Revelle, Scripps Institution of Oceanography",
    )
    prompts = capsys.readouterr().err
    assert "not an email address" in prompts
    assert "required: one of nav29, nav33" in prompts
    assert "no reader for nav99: one of nav29, nav33" in prompts
    assert "nav33  ISO timestamp, then GGA" in prompts  # the formats with a reader are listed
    assert "outside ±90" in prompts and "southernmost must be <= northernmost" in prompts
    assert "R2R" not in prompts


def test_prompted_extent_across_the_antimeridian(tmp_path, monkeypatch, capsys):
    answer(monkeypatch, "", "", "", "nav29", "", *[""] * 4, "", "", "178", "-178", "-20", "-15")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    assert load_site_config(output).extent == Extent(178.0, -178.0, -20.0, -15.0)
    assert "a box across the antimeridian" in capsys.readouterr().err


def test_writes_to_stdout(monkeypatch, capsys):
    answer(monkeypatch, "Sproul", "", "", "nav33")  # then end of input
    assert main(["site-config", "-o", "-"]) == 0
    out = capsys.readouterr().out
    assert 'name = "Sproul"' in out and 'format = "nav33"' in out


EXISTING = SiteConfig(
    vessel_name="Revelle",
    nav_format="nav33",
    nav_files="raw/POSMV_GGA-*.txt",
    nav_device=NavDevice(make="Applanix", model="POS MV", serial_number="1234"),
    extent=Extent(-125.0, -115.0, 30.0, 36.0),
    output_extra_directory="Products/NAV_QA",
    attribution="R/V Roger Revelle",
    theme={"page_size": "A4"},
)


def test_overwrites_only_with_force_starting_from_the_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "site.toml"
    output.write_text(site_config_to_toml(EXISTING))
    answer(monkeypatch, "Sproul")
    assert main(["site-config", "-o", str(output)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert load_site_config(output) == EXISTING

    # Clear the vessel name; a contact institution; keep the email; a new format
    answer(monkeypatch, "-", "Scripps", "", "nav29")
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    site = load_site_config(output)
    assert (site.vessel_name, site.contact_institution) == ("", "Scripps")
    assert site.nav_format == "nav29"
    # Kept as the defaults at the end of input
    assert site.nav_files == EXISTING.nav_files and site.extent == EXISTING.extent
    assert site.nav_device == EXISTING.nav_device
    assert site.output_extra_directory == "Products/NAV_QA"
    assert site.theme == EXISTING.theme


def test_end_of_input_writes_an_existing_file_back_unchanged(tmp_path, monkeypatch):
    output = tmp_path / "site.toml"
    text = site_config_to_toml(EXISTING)
    output.write_text(text)
    answer(monkeypatch)
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    assert output.read_text() == text


def test_end_of_input_keeps_a_default_that_wouldnt_pass_its_check():
    # No one to re-ask: a blank nav format stays blank rather than looping
    prompt = Prompter(stdin=io.StringIO(""), out=io.StringIO())
    assert prompt_site_config(SiteConfig(), prompt) == SiteConfig()


OPENVDM_YAML = """\
siteRoot: "http://127.0.0.1/"
vessel:
    name: "Roger Revelle"
    contact:
        institution: "Scripps Institution of Oceanography"
        email: ""
"""


def test_settings_in_openvdm_yaml_are_not_asked_for(tmp_path, monkeypatch, capsys):
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text(OPENVDM_YAML)
    answer(
        monkeypatch,
        "tech@example.org",  # contact email: empty in openvdm.yaml, so asked
        "nav33",
        "",  # every file
        *[""] * 4,  # no device
        "",  # no attribution
        "",  # reports extra directory
        "",  # no expected area
    )
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    site = load_site_config(output)
    assert (site.vessel_name, site.contact_institution) == ("", "")
    assert (site.contact_email, site.nav_format) == ("tech@example.org", "nav33")
    prompts = capsys.readouterr().err
    assert "vessel_name = Roger Revelle" in prompts and "Vessel name" not in prompts
    assert "Contact email" in prompts
    assert "No openvdm.yaml" not in prompts


def test_openvdm_yaml_without_a_vessel_block(tmp_path, monkeypatch, capsys):
    # OpenVDM before 2.17: every field is asked for
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text('siteRoot: "http://127.0.0.1/"\n')
    answer(monkeypatch, "Roger Revelle")
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    assert load_site_config(output).vessel_name == "Roger Revelle"
    assert "aren't asked for" not in capsys.readouterr().err


def test_says_when_openvdm_yaml_is_not_at_the_default_path(tmp_path, monkeypatch, capsys):
    # e.g. OpenVDM installed outside /opt, or the site TOML written away from the server
    answer(monkeypatch, "Roger Revelle")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    prompts = capsys.readouterr().err
    assert f"No openvdm.yaml at {tmp_path / 'missing.yaml'}" in prompts
    assert "use --openvdm-config" in prompts
    assert "Vessel name" in prompts  # every field is asked for


def test_missing_openvdm_config_is_an_error(tmp_path, capsys):
    missing = tmp_path / "elsewhere" / "openvdm.yaml"
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(missing), "-o", str(output)]) == 1
    assert f"no openvdm.yaml at {missing}" in capsys.readouterr().err
    assert not output.exists()
