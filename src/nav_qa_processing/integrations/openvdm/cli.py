"""The OpenVDM commands (#145): `openvdm` (the post-transfer hook), `config-from-openvdm` and
`site-config`, named as in sbe-qa-processing. Core's cli.py adds them with `add_commands`.
"""

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from nav_qa_processing.clitools import (
    STDOUT,
    add_no_products,
    print_written,
    refuse_overwrite,
    write_cruise_toml,
)
from nav_qa_processing.filenames import report_stem
from nav_qa_processing.integrations.openvdm.hook import DEFAULT_OPENVDM_CONFIG, OpenVDMError

logger = logging.getLogger(__name__)
# Errors that are a one-line message; in `openvdm` mode every error is one line anyway
EXPECTED_ERRORS: tuple[type[Exception], ...] = (OpenVDMError,)


def _resolve_openvdm(args: argparse.Namespace, site=None, feeds=None):
    """The current cruise's HookRuns from OpenVDM and the site config, one per feed (the site's
    feeds for the transfer, the primary first, unless given), OpenVDM's web root, and the worker
    key to set cruiseExtent with ('' when the hook won't set it)
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
    feeds = site.feeds_for(args.transfer) if feeds is None else feeds
    runs = [
        openvdm.resolve(
            cruise_config, site, args.transfer, vessel, set_extent=bool(key), feed=feed
        )
        for feed in feeds
    ]
    for run in runs:
        if run.config.nav_primary and not args.no_set_extent and not key:
            run.notes.insert(0, f"{missing_key}, so the hook can't set OpenVDM's cruise extent")
    return runs, site_root, key if any(run.sets_extent for run in runs) else ""


def _duration(text: str) -> str:
    """--min-interval's value, checked as the arguments are parsed"""
    from nav_qa_processing.integrations.openvdm.hook import duration_seconds

    try:
        duration_seconds(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return text


def _sources(run) -> str:
    return f"cruise extent from {run.extent_source}, vessel settings from {run.vessel_source}"


def _openvdm(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.openvdm import hook as openvdm

    site = openvdm.load_site_config(args.site_config)
    feeds = site.feeds_for(args.transfer)
    if not feeds:
        print(f"{args.transfer}: the site config has no [nav] feed in it; nothing to do")
        return 0
    if args.changed_files is not None:
        feeds = [f for f in feeds if openvdm.has_nav_changes(args.changed_files, f.files)]
        if not feeds:
            print(f"{args.transfer}: no new or updated nav files; nothing to do")
            return 0
    runs, site_root, key = _resolve_openvdm(args, site, feeds)
    # One feed's failure doesn't stop the others; OpenVDM shows them all, on one line
    failures = []
    primary_extent = None
    for run in runs:  # the primary first, so the others are checked against its extent
        if not run.config.nav_primary and primary_extent is not None:
            run.config = replace(run.config, extent=primary_extent)
            run.extent_source = "the primary feed"
        try:
            result = _openvdm_feed(args, run, site_root, key if run.sets_extent else "")
        except Exception as error:
            if len(runs) == 1:
                raise
            label = run.config.nav_name or "[nav]"
            logger.debug("feed %s failed", label, exc_info=True)
            failures.append(f"{label}: {type(error).__name__}: {error}")
            continue
        if result is not None and run.sets_extent:
            primary_extent = result.track_extent
    if failures:
        raise OpenVDMError(f"{len(failures)} of {len(runs)} feeds failed: {'; '.join(failures)}")
    return 0


def _openvdm_feed(args: argparse.Namespace, run, site_root: str, key: str):
    """Assesses one feed and writes its reports; its result, or None before its first files"""
    from nav_qa_processing import reports
    from nav_qa_processing.integrations.openvdm import hook as openvdm
    from nav_qa_processing.qa import run_qa
    from nav_qa_processing.theme import resolve_theme

    if not run.config.nav_format:
        feed = f" for {run.config.nav_name}" if run.config.nav_name else ""
        raise OpenVDMError(
            f"no nav format{feed}: set [nav] format in the site config (--site-config); "
            "`nav-qa-processing formats` lists them"
        )
    if not run.fileset_dir.is_dir():
        print(f"{run.fileset_dir} doesn't exist yet; nothing to do")
        return None
    if args.min_interval:
        pdf = reports.report_paths(run.output_dir, run.config.stem)["pdf"]
        written = openvdm.written_within(pdf, openvdm.duration_seconds(args.min_interval))
        if written is not None:
            print(
                f"{run.config.stem}: its reports, from {written:%Y-%m-%d %H:%M} UTC, are newer "
                f"than --min-interval {args.min_interval}; nothing to do"
            )
            return None

    print(f"{run.config.stem}: {run.fileset_dir} -> {run.output_dir} ({_sources(run)})")
    for note in run.notes:
        print(f"  note: {note}")
    theme = resolve_theme(args.theme, run.config)  # a bad theme fails before the run
    run.output_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot what OpenVDM supplied, so the notebook can re-run exactly this assessment
    snapshot = run.output_dir / f"{report_stem(run.config.stem)}_config.toml"
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
            products=not args.no_products,
        ),
    ]
    openvdm.chown_tree(run.output_dir, run.owner)
    print_written(written)
    _queue_md5_update(args, run, written)
    if key:
        _set_cruise_extent(run, site_root, key, result.track_extent)
    return result


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

    runs, site_root, _ = _resolve_openvdm(args)
    if not runs:
        raise OpenVDMError(f"the site config has no [nav] feed in {args.transfer}")
    if str(args.output) == STDOUT and len(runs) > 1:
        names = ", ".join(run.config.nav_name for run in runs)
        raise OpenVDMError(f"{args.transfer} has several feeds ({names}): give -o OUTPUT_DIR")
    for run in runs:
        print(f"{run.config.stem}: {_sources(run)}", file=sys.stderr)
        for note in run.notes:
            print(f"  note: {note}", file=sys.stderr)
        status = write_cruise_toml(openvdm.cruise_toml(run, site_root), run.config.stem, args)
        if status:
            return status
    return 0


def _site_config(args: argparse.Namespace) -> int:
    from nav_qa_processing.integrations.openvdm.hook import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from nav_qa_processing.integrations.openvdm.site_setup import prompt_site_config

    if refuse_overwrite(args.output, args.force):
        return 1
    # OpenVDM 2.17's vessel settings are the defaults where the site config has none
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
        block = load_openvdm_yaml(openvdm_yaml).get("vessel")
        from_openvdm = vessel_settings(block, "").settings
        if not block:
            # e.g. an openvdm.yaml carried over from OpenVDM 2.16 (#112)
            print(
                f"{openvdm_yaml} has no vessel block (OpenVDM 2.17 adds one: see its "
                "openvdm.yaml.dist); the vessel fields' defaults are the site TOML's, or blank.\n",
                file=sys.stderr,
            )
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


def add_commands(commands) -> None:
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
    add_no_products(hook)
    hook.add_argument(
        "--min-interval",
        metavar="DURATION",
        type=_duration,
        help=(
            "skip a feed whose reports are newer than this, e.g. 6h (s, m, h or d), so a long "
            "cruise is reassessed at most this often; leave it out of a preFinalizeCurrentCruise "
            "hook, which writes the final reports"
        ),
    )
    # OpenVDM shows the hook's failure message, so every error is one line (cli.main)
    hook.set_defaults(handler=_openvdm, every_error_one_line=True)

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
        help=(
            "directory for <CRUISE_ID>.toml, and <CRUISE_ID>_<NAME>.toml for each other feed, "
            "or - for stdout with one feed (default %(default)s)"
        ),
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
            "openvdm.yaml whose vessel settings (OpenVDM 2.17) are the defaults where the site "
            f"TOML has none (default {DEFAULT_OPENVDM_CONFIG}, when it exists)"
        ),
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)
