"""Command line interface: ``nav-qa-processing <command>``.

Conventions shared by every command:
- ``-o/--output`` is a directory. Commands that write a cruise TOML write
  ``<output>/<CRUISE_ID>_<FILESET_ID>.toml``, or to stdout for ``-o -``.
- Existing outputs aren't overwritten without ``--force``; a command checks before it does any
  work or asks any server.
- Fileset IDs are integers. ``fileset`` alone never means an ID: the directory is FILESET_DIR.
- Arguments that take a value have an uppercase metavar, and every default is in the help.
- An integration's commands are a group named after it (``r2r ...``).
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
from nav_qa_processing.network import NetworkError

STDOUT = "-"
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)  # by the number of -v


def _expected_errors() -> tuple[type[Exception], ...]:
    """Errors that are a one-line message, not a traceback: the user can act on them"""
    from nav_qa_processing.integrations.r2r.catalog import R2RError
    from nav_qa_processing.qa import ConfigError
    from nav_qa_processing.theme import ThemeError

    return (NetworkError, R2RError, ConfigError, ThemeError)


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
    _print_written(reports.write_reports(result, args.output, theme=theme))
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
    _print_written(write_r2r_reports(run, args.output, theme))
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
            "Writes a cruise TOML from R2R's catalog API for one of a cruise's GNSS filesets: "
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
    except _expected_errors() as error:
        if level <= logging.DEBUG:
            traceback.print_exc()
        print(f"nav-qa-processing: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
