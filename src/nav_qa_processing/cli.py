"""Command line interface: nav-qa-processing"""

import argparse
import sys

from nav_qa_processing import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nav-qa-processing",
        description="R2R navigation standard products and quality assessment reports",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("No subcommands yet; see README.md for the porting plan.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
