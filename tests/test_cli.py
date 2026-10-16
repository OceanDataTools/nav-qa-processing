"""The command line conventions every command follows (cli.py's docstring, #25)."""

import argparse
import logging
import tomllib

import pytest
from conftest import CONFIGS, DATA, FIXTURES

from nav_qa_processing import __version__
from nav_qa_processing.cli import build_parser, main
from nav_qa_processing.integrations.r2r import catalog
from nav_qa_processing.network import NetworkError

QA_XML = str(FIXTURES / "SP2613_169851_r2r_qa.2.0.xml")
SP2613 = DATA / "SP2613_169851_gnss"


def subcommands(parser=None, prefix="") -> dict[str, argparse.ArgumentParser]:
    """Every command, by its full name (e.g. "r2r run"), command groups included"""
    parser = parser or build_parser()
    found = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, command in action.choices.items():
                found[prefix + name] = command
                found |= subcommands(command, f"{prefix}{name} ")
    return found


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_every_argument_is_documented():
    commands = subcommands()
    assert set(commands) == {
        "formats",
        "run",
        "fetch-map-data",
        "r2r",
        "r2r run",
        "r2r config-from-catalog",
        "r2r config-from-qa",
        "r2r fetch-fileset",
    }
    for name, parser in commands.items():
        assert parser.description, name
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            label = f"{name} {action.dest}"
            assert action.help, label
            # SUPPRESS: -v/--verbose on a subcommand, whose default is the top-level one
            if action.default not in (None, False, argparse.SUPPRESS):
                assert "%(default)s" in action.help, label
            if action.nargs != 0:  # takes a value: a descriptive metavar
                assert action.metavar and action.metavar.isupper(), label
            if "-o" in action.option_strings:
                assert action.option_strings == ["-o", "--output"], label
            if "-v" in action.option_strings:
                assert action.option_strings == ["-v", "--verbose"], label


def test_every_command_takes_verbose():
    for name, parser in subcommands().items():
        assert any("-v" in a.option_strings for a in parser._actions), name


@pytest.mark.parametrize("command", ["r2r config-from-catalog", "r2r fetch-fileset"])
def test_fileset_ids_are_integers(command, capsys):
    with pytest.raises(SystemExit):
        main([*command.split(), "SP2613", "gnss"])
    assert "not an R2R fileset ID" in capsys.readouterr().err


def test_no_command_prints_help(capsys):
    assert main([]) == 1
    assert "COMMAND" in capsys.readouterr().err


def test_run_needs_a_format(tmp_path, capsys):
    assert main(["run", str(tmp_path), "-o", str(tmp_path / "out")]) == 1
    assert "give -f FORMAT_ID" in capsys.readouterr().err


def test_run_refuses_to_replace_products(tmp_path, capsys):
    output = tmp_path / "out"
    output.mkdir()
    (output / "SP2613_control.geoCSV").write_text("an earlier product")
    config = str(CONFIGS / "SP2613_169851.toml")
    for argv in (
        ["run", str(tmp_path), "-c", config, "-o", str(output)],
        ["r2r", "run", config, str(tmp_path), "-o", str(output)],
    ):
        assert main(argv) == 1
        assert "use --force" in capsys.readouterr().err
    assert (output / "SP2613_control.geoCSV").read_text() == "an earlier product"


def test_cruise_toml_output(tmp_path, capsys):
    assert main(["r2r", "config-from-qa", QA_XML, "-o", "-"]) == 0
    assert tomllib.loads(capsys.readouterr().out)["cruise"]["id"] == "SP2613"
    assert main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)]) == 0
    assert (tmp_path / "SP2613_169851.toml").exists()
    assert main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path), "--force"]) == 0


def test_catalog_refuses_before_asking_r2r(tmp_path, monkeypatch, capsys):
    (tmp_path / "SP2613_169851.toml").write_text("")
    monkeypatch.setattr(catalog, "_get", lambda *a: pytest.fail("asked R2R"))
    assert main(["r2r", "config-from-catalog", "SP2613", "169851", "-o", str(tmp_path)]) == 1
    assert "use --force" in capsys.readouterr().err


def test_expected_errors_are_one_line(monkeypatch, capsys):
    def unreachable(*args):
        raise NetworkError("couldn't reach R2R (service.rvdata.us): timed out")

    monkeypatch.setattr(catalog, "_get", unreachable)
    assert main(["r2r", "config-from-catalog", "SP2613", "169851", "-o", "-"]) == 1
    err = capsys.readouterr().err
    assert err == "nav-qa-processing: couldn't reach R2R (service.rvdata.us): timed out\n"
    assert main(["r2r", "config-from-catalog", "SP2613", "169851", "-o", "-", "-vv"]) == 1
    assert "Traceback" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["-v", "formats", "nav29"], ["formats", "nav29", "-v"]])
def test_verbose_before_or_after_the_command(argv):
    assert build_parser().parse_args(argv).verbose == 1
    logger = logging.getLogger("nav_qa_processing")
    try:
        assert main(argv) == 0
        assert logger.level == logging.INFO
    finally:
        logger.setLevel(logging.NOTSET)


def test_verbose_counts():
    assert build_parser().parse_args(["formats"]).verbose == 0
    assert build_parser().parse_args(["r2r", "fetch-fileset", "A", "1", "-vv"]).verbose == 2


def test_run_and_r2r_run_end_to_end(tmp_path, capsys):
    if not SP2613.exists():
        pytest.skip(
            "SP2613 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset SP2613 169851"
        )
    config = str(CONFIGS / "SP2613_169851.toml")
    assert main(["run", str(SP2613), "-c", config, "-o", str(tmp_path / "core")]) == 0
    assert sorted(p.name for p in (tmp_path / "core").iterdir()) == [
        "SP2613_1min.geoCSV",
        "SP2613_bestres.geoCSV",
        "SP2613_control.geoCSV",
        "SP2613_plots",
        "SP2613_qa_report.pdf",
    ]
    assert len(list((tmp_path / "core" / "SP2613_plots").glob("*.svg"))) == 4
    assert main(["r2r", "run", config, str(SP2613), "-o", str(tmp_path / "r2r")]) == 0
    out = capsys.readouterr().out
    # each run: 3 products, 4 SVGs and the PDF; then R2R's XML
    assert "R2R rating G" in out and out.count("wrote ") == 17
    assert len(list((tmp_path / "r2r").glob("SP2613_169851_qa.2.0.*Z.xml"))) == 1
