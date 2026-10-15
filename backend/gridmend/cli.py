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
    print(f"zones: {summary['zones']}")
    return 0


def cmd_zones(args: argparse.Namespace) -> int:
    from gridmend.grid.load import LIVE_GRAPH
    from gridmend.grid.zones import zone_table

    rows = zone_table(db.connect().select_graph(LIVE_GRAPH))
    print(f"{'zone':<5} {'buses':<8} {'upstream':<9} {'downstream':<20} {'ties':<26} {'kW':>5}")
    for z in rows:
        buses = z["buses"]
        span = f"{min(buses)}-{max(buses)}" if len(buses) > 1 else str(buses[0])
        print(
            f"Z{z['id']:<4} {span:<8} {', '.join(z['upstream']):<9} "
            f"{', '.join(sorted(z['downstream'])) or 'none':<20} "
            f"{', '.join(sorted(z['ties'])) or 'none':<26} {z['p_kw']:>5.0f}"
        )
    return 0


def zone_list(ids: list[int]) -> str:
    return ", ".join(f"Z{z}" for z in ids) or "none"


def bus_ranges(ids: list[int]) -> str:
    ranges, start = [], None
    for i, bus in enumerate(ids):
        if start is None:
            start = bus
        if i + 1 == len(ids) or ids[i + 1] != bus + 1:
            ranges.append(str(start) if start == bus else f"{start}-{bus}")
            start = None
    return ", ".join(ranges) or "none"


def cmd_status(args: argparse.Namespace) -> int:
    from gridmend.grid.energize import grid_status
    from gridmend.grid.load import LIVE_GRAPH

    s = grid_status(db.connect().select_graph(LIVE_GRAPH))
    print(f"energized zones: {zone_list(s['energized_zones'])}")
    print(f"dark zones:      {zone_list(s['dark_zones'])}")
    print(f"faulted zones:   {zone_list(s['faulted_zones'])}")
    print(f"dark buses:      {bus_ranges(s['dark_buses'])}")
    print(f"load served:     {s['served_kw']:.0f} / {s['total_kw']:.0f} kW")
    print("switches:")
    for sw in s["switches"]:
        moved = "" if sw["status"] == sw["normally"] else "  (off normal)"
        print(f"  {sw['id']:<6} {sw['kind']:<13} {sw['status']:<6} fi={sw['fi']}{moved}")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    from gridmend.grid.energize import set_switch
    from gridmend.grid.load import LIVE_GRAPH

    status = "closed" if args.action == "close" else "open"
    if not set_switch(db.connect().select_graph(LIVE_GRAPH), args.switch_id, status):
        print(f"no switch {args.switch_id}", file=sys.stderr)
        return 1
    print(f"{args.switch_id} {status}")
    return cmd_status(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridmend")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="check the FalkorDB connection").set_defaults(func=cmd_ping)
    sub.add_parser("basecase", help="run the IEEE 33-bus base power flow").set_defaults(
        func=cmd_basecase
    )
    sub.add_parser("load", help="write the feeder into FalkorDB").set_defaults(func=cmd_load)
    sub.add_parser("zones", help="list zones in the live graph").set_defaults(func=cmd_zones)
    sub.add_parser("status", help="show energization and switch states").set_defaults(
        func=cmd_status
    )

    switch = sub.add_parser("switch", help="operate a switch on the live graph")
    switch.add_argument("switch_id")
    switch.add_argument("action", choices=["open", "close"])
    switch.set_defaults(func=cmd_switch)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))
