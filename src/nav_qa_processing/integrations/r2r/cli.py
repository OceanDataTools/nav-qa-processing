"""The R2R commands (#145), a group under `r2r`: `run`, `config-from-catalog`,
`config-from-qa` and `fetch-fileset`. Core's cli.py adds them with `add_commands`.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from nav_qa_processing.clitools import (
    STDOUT,
    add_report_output,
    cruise_toml_path,
    print_written,
    refuse_existing_reports,
    refuse_overwrite,
    write_cruise_toml,
)
from nav_qa_processing.integrations.r2r.catalog import R2RError

# Errors that are a one-line message: R2R not having what was asked for
EXPECTED_ERRORS: tuple[type[Exception], ...] = (R2RError,)


def _r2r_run(args: argparse.Namespace) -> int:
    from nav_qa_processing import reports
    from nav_qa_processing.integrations.r2r.config import load_cruise_config
    from nav_qa_processing.integrations.r2r.reports import run_r2r, write_r2r_reports
    from nav_qa_processing.theme import resolve_theme

    # The XML's name has the run's time, so only the products can already exist
    config = load_cruise_config(args.cruise_toml)
    theme = resolve_theme(args.theme, config)  # a bad theme fails before the run
    paths = list(reports.report_paths(args.output, config.stem, not args.no_products).values())
    if args.r2rnav:
        from nav_qa_processing.integrations.r2r import r2rnav

        paths += r2rnav.paths(args.output, config.stem)
    if refuse_existing_reports(paths, args.force):
        return 1
    run = run_r2r(args.fileset_dir, args.cruise_toml)
    print(run)
    written = write_r2r_reports(
        run,
        args.output,
        theme,
        args.theme,
        args.execute_notebook,
        r2rnav=args.r2rnav,
        products=not args.no_products,
    )
    print_written(written)
    return 0


def _r2r_config_from_catalog(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.catalog import config_from_r2r_catalog
    from nav_qa_processing.integrations.r2r.config import config_to_toml

    name = f"{args.cruise_id}_{args.fileset_id}"
    if str(args.output) != STDOUT and refuse_overwrite(
        cruise_toml_path(args.output, name), args.force
    ):
        return 1  # before asking R2R
    config = config_from_r2r_catalog(args.cruise_id, args.fileset_id, not args.no_qa_report)
    lines = [f"# From R2R's catalog, {datetime.now(UTC):%Y-%m-%d}"]
    if not config.nav_format:
        lines.append(f"# No reader for R2R format {config.format_id}: set [nav] format")
        print(f"warning: no reader for R2R format {config.format_id}", file=sys.stderr)
    return write_cruise_toml("\n".join(lines) + "\n" + config_to_toml(config), name, args)


def _r2r_config_from_qa(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.catalog import config_from_qa_report
    from nav_qa_processing.integrations.r2r.config import config_to_toml

    config = config_from_qa_report(args.r2r_qa_xml)
    text = f"# From {args.r2r_qa_xml.name}\n" + config_to_toml(config)
    return write_cruise_toml(text, config.identifier, args)


def _r2r_fetch_fileset(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.r2r.download import download_fileset

    destination = args.output / f"{args.cruise_id}_{args.fileset_id}_gnss"
    if refuse_overwrite(destination, args.force):
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


def add_commands(commands) -> None:
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
            "<CRUISE_ID>_<FILESET_ID>_qa.2.0.<time>.xml, as R2R names them but for the "
            "time's colons (2026-07-31T200556Z)."
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
    add_report_output(run)
    run.add_argument(
        "--r2rnav",
        action="store_true",
        help=(
            "also write the products as legacy r2rnav (<CRUISE_ID>_bestres|1min|control.r2rnav), "
            "the format NavManager wrote them in"
        ),
    )
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
