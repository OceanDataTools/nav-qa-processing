"""Command line interface: ``nav-qa-processing <command>``.

Conventions shared by every command:
- ``-o/--output`` is a directory, except for ``site-config``, which writes one file. Commands
  that write a cruise TOML write ``<output>/<CRUISE_ID>_<FILESET_ID>.toml``, or to stdout for
  ``-o -``.
- Existing outputs aren't overwritten without ``--force``; a command checks before it does any
  work or asks any server.
- Fileset IDs are integers. ``fileset`` alone never means an ID: the directory is FILESET_DIR.
- Arguments that take a value have an uppercase metavar, and every default is in the help.
- An integration's commands are a group named after it (``r2r ...``), except OpenVDM's
  (``openvdm``, ``config-from-openvdm``, ``site-config``), which are named as in
  sbe-qa-processing so a ship's ``openvdm.yaml`` calls both tools alike. The hook's arguments are called from installed
  ``openvdm.yaml`` files, so they keep their names. In ``openvdm`` mode every error, expected or
  not, is one line on stderr, because OpenVDM displays it; the hook replaces earlier reports
  without ``--force``.
- ``-v/--verbose``, before or after the command, logs more: ``-v`` is INFO and ``-vv`` DEBUG
  (default WARNING), for the ``nav_qa_processing`` loggers. Expected errors (a server that can't
  be reached, R2R not having what was asked for, a config that can't run the QA, a bad theme)
  are one line on stderr and exit 1; ``-vv`` adds their traceback.
"""

import argparse
import logging
import sys
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from nav_qa_processing import __version__
from nav_qa_processing.integrations.openvdm.hook import DEFAULT_OPENVDM_CONFIG, OpenVDMError
from nav_qa_processing.network import NetworkError

STDOUT = "-"
logger = logging.getLogger(__name__)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)  # by the number of -v


def _expected_errors() -> tuple[type[Exception], ...]:
    """Errors that are a one-line message, not a traceback: the user can act on them"""
    from nav_qa_processing.integrations.r2r.catalog import R2RError
    from nav_qa_processing.qa import ConfigError
    from nav_qa_processing.theme import ThemeError

    return (NetworkError, R2RError, OpenVDMError, ConfigError, ThemeError)


def _refuse_overwrite(path: Path, force: bool) -> bool:
    if str(path) != STDOUT and path.exists() and not force:
        print(f"{path} exists; use --force to overwrite it", file=sys.stderr)
        return True
    return False


def _write_cruise_toml(text: str, name: str, args: argparse.Namespace) -> int:
    """text as <output>/<name>.toml, or to stdout for -o -"""
    if str(args.output) == STDOUT:
        print(text, end="")
        return 0
    destination = args.output / f"{name}.toml"
    if _refuse_overwrite(destination, args.force):
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    print(f"wrote {destination}", file=sys.stderr)
    return 0


def _refuse_existing_reports(paths, force: bool) -> bool:
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        print(f"{existing[0]} exists; use --force to overwrite the reports", file=sys.stderr)
        return True
    return False


def _print_written(written: list[Path]) -> None:
    for path in written:
        print(f"wrote {path}")


# Commands ------------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from nav_qa_processing import reports
    from nav_qa_processing.qa import resolve_config, run_qa
    from nav_qa_processing.theme import resolve_theme

    config = resolve_config(args.config, args.fileset_dir)
    if args.format:
        config = replace(config, nav_format=args.format)
    if not config.nav_format:
        print(
            "nav-qa-processing: no nav format: give -f FORMAT_ID or set [nav] format in a "
            "config (`nav-qa-processing formats` lists them)",
            file=sys.stderr,
        )
        return 1
    theme = resolve_theme(args.theme, config)  # a bad theme fails before the run
    paths = reports.report_paths(args.output, config.cruise_id)
    if _refuse_existing_reports(paths.values(), args.force):
        return 1
    result = run_qa(args.fileset_dir, config)
    result.config_path = args.config
    print(result)
    _print_written(
        reports.write_reports(
            result,
            args.output,
            theme=theme,
            theme_file=args.theme,
            execute_notebook=args.execute_notebook,
        )
    )
    return 0


def _fetch_map_data(args: argparse.Namespace) -> int:
    from nav_qa_processing import maps

    for layer in maps.prefetch():
        print(f"cached Natural Earth {layer}")
    return 0


def _formats(args: argparse.Namespace) -> int:
    from nav_qa_processing import formats
    from nav_qa_processing.formats import catalog

    if args.id is None:
        specs = formats.all_formats()
        width = max(len(i) for i in specs)
        print(f"{'ID':{width}}  Reader  Title")
        for format_id, spec in specs.items():
            reader = "yes" if spec.supported else "-"
            print(f"{format_id:{width}}  {reader:6}  {spec.definition.title}")
        print(f"\nSentences: {', '.join(catalog.sentences())}")
        print("Show one with: nav-qa-processing formats <ID>")
        return 0
    definition = catalog.formats().get(args.id) or catalog.sentences().get(args.id.upper())
    if definition is None:
        print(f"Unknown format or sentence: {args.id}", file=sys.stderr)
        return 1
    print(catalog.describe(definition), end="")
    return 0


def _resolve_openvdm(args: argparse.Namespace, site=None):
    """The current cruise's HookRun from OpenVDM and the site config, OpenVDM's web root, and
    the worker key to set cruiseExtent with ('' when the hook won't set it)
    """
    from nav_qa_processing.integrations.openvdm import hook as openvdm

    if args.site_root:
        # No openvdm.yaml: resolve reads the vessel block from the cruise's ovdmConfig.json
        site_root, vessel, key = args.site_root, None, ""
        missing_key = "with --site-root there's no openvdm.yaml workerApiKey"
    else:
        settings = openvdm.load_openvdm_yaml(args.openvdm_config)
        site_root = openvdm.site_root_from_config(settings)
        vessel = openvdm.vessel_settings(settings.get("vessel"), "openvdm.yaml")
        key = openvdm.worker_key(settings)
        missing_key = "openvdm.yaml has no workerApiKey (OpenVDM 2.17)"
    if args.no_set_extent:
        key = ""
    cruise_config = openvdm.fetch_cruise_config(site_root)
    site = site or openvdm.load_site_config(args.site_config)
    run = openvdm.resolve(cruise_config, site, args.transfer, vessel, set_extent=bool(key))
    if not args.no_set_extent and not key:
        run.notes.insert(0, f"{missing_key}, so the hook can't set OpenVDM's cruise extent")
    return run, site_root, key if run.sets_extent else ""


def _sources(run) -> str:
    return f"cruise extent from {run.extent_source}, vessel settings from {run.vessel_source}"


def _openvdm(args: argparse.Namespace) -> int:
    from nav_qa_processing import reports
    from nav_qa_processing.integrations.openvdm import hook as openvdm
    from nav_qa_processing.qa import run_qa
    from nav_qa_processing.theme import resolve_theme

    site = openvdm.load_site_config(args.site_config)
    if args.changed_files is not None and not openvdm.has_nav_changes(
        args.changed_files, site.nav_files
    ):
        print(f"{args.transfer}: no new or updated nav files; nothing to do")
        return 0
    run, site_root, key = _resolve_openvdm(args, site)
    if not run.config.nav_format:
        raise OpenVDMError(
            "no nav format: set [nav] format in the site config (--site-config); "
            "`nav-qa-processing formats` lists them"
        )
    if not run.fileset_dir.is_dir():
        print(f"{run.fileset_dir} doesn't exist yet; nothing to do")
        return 0

    print(f"{run.config.cruise_id}: {run.fileset_dir} -> {run.output_dir} ({_sources(run)})")
    for note in run.notes:
        print(f"  note: {note}")
    theme = resolve_theme(args.theme, run.config)  # a bad theme fails before the run
    run.output_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot what OpenVDM supplied, so the notebook can re-run exactly this assessment
    snapshot = run.output_dir / f"{run.config.cruise_id}_config.toml"
    snapshot.write_text(openvdm.cruise_toml(run, site_root))
    result = run_qa(run.fileset_dir, snapshot)
    print(result)
    written = [
        snapshot,
        *reports.write_reports(
            result,
            run.output_dir,
            theme=theme,
            theme_file=args.theme,
            execute_notebook=args.execute_notebook,
        ),
    ]
    openvdm.chown_tree(run.output_dir, run.owner)
    _print_written(written)
    _queue_md5_update(args, run, written)
    if key:
        _set_cruise_extent(run, site_root, key, result.track_extent)
    return 0


def _set_cruise_extent(run, site_root: str, key: str, extent) -> None:
    """Sets OpenVDM's cruiseExtent from the fixes that passed QC. The QA itself succeeded, so a
    failure here is a warning, not a failed hook
    """
    from nav_qa_processing.integrations.openvdm import hook as openvdm

    if extent is None:
        logger.warning("no fixes passed QC, so OpenVDM's cruise extent is left as it is")
        return
    problem = openvdm.set_cruise_extent(site_root, run.config.cruise_id, extent, key)
    if problem:
        logger.warning("OpenVDM's cruise extent wasn't set: %s", problem)
    else:
        print(f"set OpenVDM's cruise extent to {extent}")


def _queue_md5_update(args: argparse.Namespace, run, written: list[Path]) -> None:
    """Adds the reports to OpenVDM's MD5 summary. The QA itself succeeded, so a failure here
    is a warning, not a failed hook
    """
    from nav_qa_processing.integrations.openvdm import hook as openvdm

    # With --site-root there's no openvdm.yaml to find OpenVDM by; try the default install
    openvdm_yaml = DEFAULT_OPENVDM_CONFIG if args.site_root else args.openvdm_config
    problem = openvdm.queue_md5_update(openvdm.openvdm_install_dir(openvdm_yaml), run, written)
    if problem:
        logger.warning("the reports aren't in OpenVDM's MD5 summary: %s", problem)
    else:
        print(f"queued an MD5 summary update for the reports in {run.output_dir}")


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.openvdm import hook as openvdm

    run, site_root, _ = _resolve_openvdm(args)
    print(f"{run.config.cruise_id}: {_sources(run)}", file=sys.stderr)
    for note in run.notes:
        print(f"  note: {note}", file=sys.stderr)
    return _write_cruise_toml(openvdm.cruise_toml(run, site_root), run.config.cruise_id, args)


def _site_config(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.openvdm.hook import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from nav_qa_processing.integrations.openvdm.site_setup import prompt_site_config

    if _refuse_overwrite(args.output, args.force):
        return 1
    # OpenVDM 2.17's vessel settings win over the site config's, so they aren't asked for
    openvdm_yaml = args.openvdm_config
    if openvdm_yaml is not None and not openvdm_yaml.exists():
        raise OpenVDMError(f"no openvdm.yaml at {openvdm_yaml}; check --openvdm-config")
    if openvdm_yaml is None and DEFAULT_OPENVDM_CONFIG.exists():
        openvdm_yaml = DEFAULT_OPENVDM_CONFIG
    elif openvdm_yaml is None:
        # Not an error: the site TOML may be written away from the OpenVDM server
        print(
            f"No openvdm.yaml at {DEFAULT_OPENVDM_CONFIG}; asking for every field. If OpenVDM "
            "2.17 is installed elsewhere, use --openvdm-config so its vessel settings aren't "
            "asked for.\n",
            file=sys.stderr,
        )
    from_openvdm = {}
    if openvdm_yaml is not None:
        from_openvdm = vessel_settings(load_openvdm_yaml(openvdm_yaml).get("vessel"), "").settings
    # Re-running over an existing file starts from its values
    existing = str(args.output) != STDOUT and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    text = site_config_to_toml(prompt_site_config(defaults, from_openvdm=from_openvdm))
    if str(args.output) == STDOUT:
        print(text, end="")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


def _r2r_run(args: argparse.Namespace) -> int:
    from nav_qa_processing import reports
    from nav_qa_processing.integrations.r2r.config import load_cruise_config
    from nav_qa_processing.integrations.r2r.reports import run_r2r, write_r2r_reports
    from nav_qa_processing.theme import resolve_theme

    # The XML's name has the run's time, so only the products can already exist
    config = load_cruise_config(args.cruise_toml)
    theme = resolve_theme(args.theme, config)  # a bad theme fails before the run
    paths = reports.report_paths(args.output, config.cruise_id)
    if _refuse_existing_reports(paths.values(), args.force):
        return 1
    run = run_r2r(args.fileset_dir, args.cruise_toml)
    print(run)
    _print_written(write_r2r_reports(run, args.output, theme, args.theme, args.execute_notebook))
    return 0


def _r2r_config_from_catalog(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.catalog import config_from_r2r_catalog
    from nav_qa_processing.integrations.r2r.config import config_to_toml

    name = f"{args.cruise_id}_{args.fileset_id}"
    if str(args.output) != STDOUT and _refuse_overwrite(args.output / f"{name}.toml", args.force):
        return 1  # before asking R2R
    config = config_from_r2r_catalog(args.cruise_id, args.fileset_id, not args.no_qa_report)
    lines = [f"# From R2R's catalog, {datetime.now(UTC):%Y-%m-%d}"]
    if not config.nav_format:
        lines.append(f"# No reader for R2R format {config.format_id}: set [nav] format")
        print(f"warning: no reader for R2R format {config.format_id}", file=sys.stderr)
    return _write_cruise_toml("\n".join(lines) + "\n" + config_to_toml(config), name, args)


def _r2r_config_from_qa(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.catalog import config_from_qa_report
    from nav_qa_processing.integrations.r2r.config import config_to_toml

    config = config_from_qa_report(args.r2r_qa_xml)
    text = f"# From {args.r2r_qa_xml.name}\n" + config_to_toml(config)
    return _write_cruise_toml(text, config.identifier, args)


def _r2r_fetch_fileset(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.download import download_fileset

    destination = args.output / f"{args.cruise_id}_{args.fileset_id}_gnss"
    if _refuse_overwrite(destination, args.force):
        return 1
    download = download_fileset(args.cruise_id, args.fileset_id, destination)
    print(f"{download.files} raw files from {download.bag_url} -> {destination}")
    if download.qa_report:
        print(f"R2R's QA report -> {download.qa_report}")
    else:
        print("R2R has no QA 2.0 report for this fileset", file=sys.stderr)
    if download.productset_id:
        print(
            f"{download.product_files} product files (fileset {download.productset_id}) "
            f"-> {destination / 'products'}"
        )
    else:
        print("R2R has no r2rnav product for this cruise", file=sys.stderr)
    return 0


def _add_verbose(parser: argparse.ArgumentParser, default=0) -> None:
    """-v/--verbose on parser and, so it also works after the command, on every subcommand.
    The subcommands' default is SUPPRESS, so they don't reset a -v given before the command
    """
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=default,
        help="log more: -v for INFO, -vv for DEBUG and tracebacks of expected errors",
    )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for command in action.choices.values():
                _add_verbose(command, argparse.SUPPRESS)


def _fileset_id(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an R2R fileset ID (an integer): {text!r}") from None


def _add_r2r_ids(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("cruise_id", metavar="CRUISE_ID", help="R2R cruise ID, e.g. SP2613")
    parser.add_argument(
        "fileset_id",
        metavar="FILESET_ID",
        type=_fileset_id,
        help="R2R's raw GNSS fileset ID, e.g. 169851",
    )


def _add_cruise_toml_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("configs"),
        help="directory for <CRUISE_ID>_<FILESET_ID>.toml, or - for stdout (default %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing cruise TOML")


def _add_openvdm_arguments(parser: argparse.ArgumentParser) -> None:
    """Where to find OpenVDM and the site config, shared by the hook and config-from-openvdm"""
    parser.add_argument(
        "transfer",
        metavar="TRANSFER",
        help="collection system transfer name, e.g. {collectionSystemTransferName}",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        default=DEFAULT_OPENVDM_CONFIG,
        help="openvdm.yaml, for OpenVDM's siteRoot and vessel settings (default %(default)s)",
    )
    source.add_argument(
        "--site-root",
        metavar="URL",
        help=(
            "OpenVDM's web root URL, instead of --openvdm-config; the vessel settings then come "
            "from the cruise's ovdmConfig.json"
        ),
    )
    parser.add_argument(
        "--site-config",
        metavar="SITE_TOML",
        type=Path,
        help="per-ship TOML: the [nav] format and files, vessel, extent, ...",
    )
    parser.add_argument(
        "--no-set-extent",
        action="store_true",
        help=(
            "don't set OpenVDM's cruiseExtent from the fixes that pass QC; check the fixes "
            "against it (else the site config's [extent]) instead"
        ),
    )


def _add_openvdm_commands(commands) -> None:
    hook = commands.add_parser(
        "openvdm",
        help="assess the current cruise's GNSS feed from an OpenVDM post-transfer hook",
        description=(
            "Reads the current cruise from OpenVDM's API, assesses the collection system "
            "transfer's nav files ([nav] in the site config), and writes the products and "
            "reports into an OpenVDM extra directory, replacing earlier ones."
        ),
    )
    _add_openvdm_arguments(hook)
    hook.add_argument(
        "--changed-files",
        action="append",
        metavar="FILES",
        help=(
            "OpenVDM's {newFiles}/{updatedFiles}; repeatable. When given, the QA only runs if "
            "they include nav files ([nav] files). Use the --changed-files={newFiles} form, so "
            "an empty list isn't dropped"
        ),
    )
    hook.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help="theme file for the PDF, figures and notebook; the site config's [theme] applies on top",
    )
    hook.add_argument(
        "--execute-notebook",
        action="store_true",
        help="run the notebook and save its outputs (needs nbclient and ipykernel)",
    )
    hook.set_defaults(handler=_openvdm)

    from_openvdm = commands.add_parser(
        "config-from-openvdm",
        help="make a config TOML from OpenVDM's current cruise, without running the QA",
        description=(
            "Merges OpenVDM's current cruise with the site config, as the openvdm hook does, "
            "and writes the config TOML for review and `run -c`. Reports what's missing or "
            "assumed."
        ),
    )
    _add_openvdm_arguments(from_openvdm)
    from_openvdm.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("configs"),
        help="directory for <CRUISE_ID>.toml, or - for stdout (default %(default)s)",
    )
    from_openvdm.add_argument(
        "--force", action="store_true", help="overwrite an existing config TOML"
    )
    from_openvdm.set_defaults(handler=_config_from_openvdm)

    site = commands.add_parser(
        "site-config",
        help="make the per-ship site TOML for the openvdm hook, by prompting for each field",
        description=(
            "Prompts for the per-ship settings OpenVDM doesn't store: the vessel name and "
            "report contact (unless openvdm.yaml has them, from OpenVDM 2.17), the GNSS feed's "
            "format and files, the GeoCSV attribution, the reports' extra directory and an "
            "expected cruise area. Works offline. Over an existing file (with --force), its "
            "values are the defaults."
        ),
    )
    site.add_argument(
        "-o",
        "--output",
        metavar="SITE_TOML",
        type=Path,
        default=Path("configs/site.toml"),
        help="site TOML to write, or - for stdout (default %(default)s)",
    )
    site.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        help=(
            "openvdm.yaml whose vessel settings (OpenVDM 2.17) aren't asked for again (default "
            f"{DEFAULT_OPENVDM_CONFIG}, when it exists)"
        ),
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)


def _add_r2r_commands(commands) -> None:
    group = commands.add_parser(
        "r2r",
        help="Rolling Deck to Repository: R2R's tests and QA XML, cruise configs, filesets",
        description=(
            "R2R's navigation tests, ratings and QA 2.0 XML, cruise TOMLs from R2R's catalog, "
            "and downloads of released GNSS filesets."
        ),
    )
    commands = group.add_subparsers(
        dest="r2r_command", required=True, metavar="COMMAND", help="R2R command"
    )

    run = commands.add_parser(
        "run",
        help="assess a GNSS fileset with R2R's tests; write the products and R2R QA XML",
        description=(
            "Runs the QA on a GNSS fileset with a cruise TOML, then R2R's six tests. Writes "
            "<CRUISE_ID>_bestres|1min|control.geoCSV and "
            "<CRUISE_ID>_<FILESET_ID>_qa.2.0.<time>.xml, as R2R names them."
        ),
    )
    run.add_argument(
        "cruise_toml",
        metavar="CRUISE_TOML",
        type=Path,
        help="cruise TOML with R2R's IDs, e.g. from `r2r config-from-catalog`",
    )
    run.add_argument(
        "fileset_dir",
        metavar="FILESET_DIR",
        type=Path,
        help="BagIt bag or directory of GNSS files",
    )
    _add_report_output(run)
    run.set_defaults(handler=_r2r_run)

    from_catalog = commands.add_parser(
        "config-from-catalog",
        help="make a cruise TOML from R2R's catalog",
        description=(
            "Writes a cruise TOML from R2R's catalog API for one of a cruise's navigation (GNSS or INS) filesets: "
            "the cruise's name, vessel, operator, dates, ports, DOIs and bounding box, the "
            "receiver and the nav format. The API has no port coordinates, format alias or "
            "catalog dates; those come from R2R's QA report for the fileset, when it has one."
        ),
    )
    _add_r2r_ids(from_catalog)
    _add_cruise_toml_output(from_catalog)
    from_catalog.add_argument(
        "--no-qa-report",
        action="store_true",
        help="use the catalog API only, not R2R's QA report",
    )
    from_catalog.set_defaults(handler=_r2r_config_from_catalog)

    from_qa = commands.add_parser(
        "config-from-qa",
        help="make a cruise TOML from an existing R2R QA 2.0 XML",
        description=(
            "Writes a cruise TOML from an R2R QA 2.0 report's provenance and fileset info, for "
            "checking this tool's results against R2R's."
        ),
    )
    from_qa.add_argument(
        "r2r_qa_xml", metavar="R2R_QA_XML", type=Path, help="R2R's QA 2.0 XML for the fileset"
    )
    _add_cruise_toml_output(from_qa)
    from_qa.set_defaults(handler=_r2r_config_from_qa)

    fetch = commands.add_parser(
        "fetch-fileset",
        help="download a released R2R GNSS fileset, R2R's QA report and nav products",
        description=(
            "Downloads a released R2R GNSS fileset (a BagIt bag) into "
            "<OUTPUT_DIR>/<CRUISE_ID>_<FILESET_ID>_gnss/, with R2R's QA report beside it as "
            "r2r_qa.2.0.xml and R2R's r2rnav product bag in products/, e.g. for the "
            "validation tests."
        ),
    )
    _add_r2r_ids(fetch)
    fetch.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("data"),
        help="directory for the fileset's directory (default %(default)s)",
    )
    fetch.add_argument(
        "--force", action="store_true", help="download into an existing fileset directory"
    )
    fetch.set_defaults(handler=_r2r_fetch_fileset)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nav-qa-processing",
        description="R2R navigation standard products and quality assessment reports",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    formats = commands.add_parser(
        "formats",
        help="list the raw navigation formats, or describe one",
        description=(
            "Lists R2R's raw navigation format IDs (nav1 … nav33, uhdas, siomet, metoc_nav) "
            "and whether this version can read them, or describes one format or NMEA sentence."
        ),
    )
    formats.add_argument(
        "id", metavar="ID", nargs="?", help="a format ID or sentence, e.g. nav29, GGA"
    )
    formats.set_defaults(handler=_formats)

    run = commands.add_parser(
        "run",
        help="assess a GNSS fileset and write the products",
        description=(
            "Reads a fileset's primary GNSS feed, flags bad fixes, computes the QA statistics "
            "and core checks, and writes <CRUISE_ID>_bestres|1min|control.geoCSV."
        ),
    )
    run.add_argument(
        "fileset_dir",
        metavar="FILESET_DIR",
        type=Path,
        help="BagIt bag or directory of GNSS files",
    )
    run.add_argument(
        "-c",
        "--config",
        metavar="CONFIG_TOML",
        type=Path,
        help="config TOML; without one, the products are named after FILESET_DIR",
    )
    run.add_argument(
        "-f",
        "--format",
        metavar="FORMAT_ID",
        help="the GNSS files' format (see `formats`), e.g. nav29; overrides the config's [nav]",
    )
    _add_report_output(run)
    run.set_defaults(handler=_run)

    maps = commands.add_parser(
        "fetch-map-data",
        help="download the Natural Earth map layers, for working offline",
        description=(
            "Caches the Natural Earth layers the trackline map uses (1:10m land, lakes and "
            "coastlines, and the 1:110m world for the inset, about 27 MB) in cartopy's data "
            "directory, e.g. before going to sea."
        ),
    )
    maps.set_defaults(handler=_fetch_map_data)

    _add_openvdm_commands(commands)
    _add_r2r_commands(commands)
    _add_verbose(parser)
    return parser


def _add_report_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("output"),
        help="directory for the products and reports (default %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing reports")
    parser.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help="theme file for the PDF, figures and notebook; the config's [theme] applies on top",
    )
    parser.add_argument(
        "--execute-notebook",
        action="store_true",
        help="run the notebook and save its outputs (needs nbclient and ipykernel)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    level = LOG_LEVELS[min(args.verbose, len(LOG_LEVELS) - 1)]
    if args.verbose:
        logging.getLogger("nav_qa_processing").setLevel(level)
    # Render figures off screen; the notebook uses its own inline backend
    import matplotlib

    matplotlib.use("Agg")
    try:
        return args.handler(args)
    except Exception as error:
        hook = args.command == "openvdm"  # OpenVDM shows the hook's failure message
        if not hook and not isinstance(error, _expected_errors()):
            raise
        if level <= logging.DEBUG:
            traceback.print_exc()
        message = f"{type(error).__name__}: {error}" if hook else str(error)
        print(f"nav-qa-processing: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
