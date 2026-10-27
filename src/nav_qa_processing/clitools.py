"""What the commands share, core's and the integrations' (#145): refusing to overwrite, writing
a cruise TOML or reports, and the report-output arguments. The conventions are in cli.py.
"""

import argparse
import sys
from pathlib import Path

from nav_qa_processing.filenames import report_stem

STDOUT = "-"


def refuse_overwrite(path: Path, force: bool) -> bool:
    if str(path) != STDOUT and path.exists() and not force:
        print(f"{path} exists; use --force to overwrite it", file=sys.stderr)
        return True
    return False


def cruise_toml_path(output: Path, name: str) -> Path:
    """<output>/<name>.toml, a valid file name whatever the name"""
    return Path(output) / f"{report_stem(name)}.toml"


def write_cruise_toml(text: str, name: str, args: argparse.Namespace) -> int:
    """text as <output>/<name>.toml, or to stdout for -o -"""
    if str(args.output) == STDOUT:
        print(text, end="")
        return 0
    destination = cruise_toml_path(args.output, name)
    if refuse_overwrite(destination, args.force):
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    print(f"wrote {destination}", file=sys.stderr)
    return 0


def refuse_existing_reports(paths, force: bool) -> bool:
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        print(f"{existing[0]} exists; use --force to overwrite the reports", file=sys.stderr)
        return True
    return False


def print_written(written: list[Path]) -> None:
    for path in written:
        print(f"wrote {path}")


def add_report_output(parser: argparse.ArgumentParser) -> None:
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
