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
from pathlib import Path

from nav_qa_processing import __version__
from nav_qa_processing.clitools import add_report_output, print_written, refuse_existing_reports
from nav_qa_processing.formats.catalog import canonical_id
from nav_qa_processing.integrations.openvdm import cli as openvdm_cli
from nav_qa_processing.integrations.r2r import cli as r2r_cli
from nav_qa_processing.network import NetworkError

# The integrations whose commands this CLI adds (each module's add_commands), in help order
INTEGRATIONS = (openvdm_cli, r2r_cli)
logger = logging.getLogger(__name__)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)  # by the number of -v


def _expected_errors() -> tuple[type[Exception], ...]:
    """Errors that are a one-line message, not a traceback: the user can act on them; core's
    and each integration's (its EXPECTED_ERRORS)
    """
    from nav_qa_processing.qa import ConfigError
    from nav_qa_processing.theme import ThemeError

    integrations = tuple(e for i in INTEGRATIONS for e in i.EXPECTED_ERRORS)
    return (NetworkError, ConfigError, ThemeError, *integrations)


# Commands ------------------------------------------------------------------------------------


def _run_configs(args: argparse.Namespace) -> list:
    """The feeds to run: the config's (one with --nav), or one without a config"""
    from nav_qa_processing.config import load_config, load_configs
    from nav_qa_processing.qa import ConfigError, resolve_config

    if args.config is None:
        if args.nav:
            raise ConfigError("--nav picks one of a config's [[nav]] feeds: give -c CONFIG_TOML")
        configs = [None]
    else:
        try:
            configs = (
                [load_config(args.config, nav=args.nav)] if args.nav else load_configs(args.config)
            )
        except ValueError as error:  # e.g. no such feed, or [[nav]] without names
            raise ConfigError(str(error)) from None
    if args.format and len(configs) > 1:
        names = ", ".join(c.nav_name for c in configs)
        raise ConfigError(f"-f applies to one feed: pick one of {names} with --nav")
    configs = [resolve_config(config, args.fileset_dir) for config in configs]
    if args.format:
        configs = [replace(configs[0], nav_format=canonical_id(args.format))]
    return configs


def _run(args: argparse.Namespace) -> int:
    from nav_qa_processing import reports
    from nav_qa_processing.qa import run_qa
    from nav_qa_processing.theme import resolve_theme

    configs = _run_configs(args)
    for config in configs:
        if not config.nav_format:
            feed = f" for [[nav]] {config.nav_name}" if config.nav_name else ""
            print(
                f"nav-qa-processing: no nav format{feed}: give -f FORMAT_ID or set [nav] format "
                "in a config (`nav-qa-processing formats` lists them)",
                file=sys.stderr,
            )
            return 1
    themes = [resolve_theme(args.theme, config) for config in configs]  # fail before a run
    paths = [p for c in configs for p in reports.report_paths(args.output, c.stem).values()]
    if refuse_existing_reports(paths, args.force):
        return 1
    for config, theme in zip(configs, themes, strict=True):
        result = run_qa(args.fileset_dir, config)
        result.config_path = args.config
        print(result)
        print_written(
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
    definition = catalog.formats().get(canonical_id(args.id)) or catalog.sentences().get(
        args.id.upper()
    )
    if definition is None:
        print(f"Unknown format or sentence: {args.id}", file=sys.stderr)
        return 1
    print(catalog.describe(definition), end="")
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
            "Lists R2R's raw navigation format IDs (nav01 … nav33, uhdas, siomet, metoc_nav) "
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
    run.add_argument(
        "--nav",
        metavar="NAME",
        help=(
            "run only the config's [[nav]] feed of this name; without it, every feed is run "
            "and a non-primary feed's outputs are named <CRUISE_ID>_<NAME>_..."
        ),
    )
    add_report_output(run)
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

    # The integrations' commands (#145): this is the one place core's CLI meets them
    for integration in INTEGRATIONS:
        integration.add_commands(commands)
    _add_verbose(parser)
    return parser


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
        # A command that sets every_error_one_line (the OpenVDM hook, whose failure message
        # OpenVDM shows) reports even an unexpected error in one line
        hook = getattr(args, "every_error_one_line", False)
        if not hook and not isinstance(error, _expected_errors()):
            raise
        if level <= logging.DEBUG:
            traceback.print_exc()
        message = f"{type(error).__name__}: {error}" if hook else str(error)
        print(f"nav-qa-processing: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
