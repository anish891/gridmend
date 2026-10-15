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


def cmd_basecase(args: argparse.Namespace) -> int:
    from gridmend.grid.case33 import run_base_case

    case = run_base_case()
    print(f"buses:        {case.buses}")
    print(f"closed lines: {len(case.closed_lines)}")
    print(f"ties:         {', '.join(case.ties)}")
    print(f"load:         {case.load_kw:.0f} kW")
    print(f"losses:       {case.losses_kw:.1f} kW")
    print(f"min voltage:  {case.min_v_pu:.4f} pu at bus {case.min_v_bus}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    from gridmend.grid.load import BASE_GRAPH, LIVE_GRAPH, write_graph

    summary = write_graph(db.connect())
    sw = summary["sw"]
    print(f"wrote {BASE_GRAPH} and {LIVE_GRAPH}")
    print(f"buses: {summary['buses']}")
    print(f"SEG:   {summary['seg']}")
    print(f"SW:    {sum(sw.values())} ({', '.join(f'{n} {k}' for k, n in sw.items())})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridmend")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="check the FalkorDB connection").set_defaults(func=cmd_ping)
    sub.add_parser("basecase", help="run the IEEE 33-bus base power flow").set_defaults(
        func=cmd_basecase
    )
    sub.add_parser("load", help="write the feeder into FalkorDB").set_defaults(func=cmd_load)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))
