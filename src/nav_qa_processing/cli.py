"""Command line interface: nav-qa-processing"""

import argparse
import sys

from nav_qa_processing import __version__


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
