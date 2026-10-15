import argparse
import sys

from gridmend import db


def cmd_ping(args: argparse.Namespace) -> int:
    try:
        graphs = db.ping()
    except Exception as exc:
        print(f"FalkorDB unreachable: {exc}", file=sys.stderr)
        return 1
    print(f"OK ({len(graphs)} graphs)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridmend")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="check the FalkorDB connection").set_defaults(func=cmd_ping)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))
