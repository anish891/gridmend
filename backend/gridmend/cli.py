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


def cmd_fault(args: argparse.Namespace) -> int:
    from gridmend.grid.load import LIVE_GRAPH
    from gridmend.sim import FaultError, apply_fault

    try:
        path = apply_fault(db.connect().select_graph(LIVE_GRAPH), args.line)
    except FaultError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"fault on {args.line}; breaker tripped")
    print(f"indicators set: {', '.join(path)}")
    return cmd_status(args)


def cmd_locate(args: argparse.Namespace) -> int:
    from gridmend.flisr.locate import LocateError, locate_fault
    from gridmend.grid.load import LIVE_GRAPH

    try:
        location = locate_fault(db.connect().select_graph(LIVE_GRAPH))
    except LocateError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"faulted zone: Z{location.zone}")
    print(f"evidence:     {' -> '.join(location.chain)} (all set; downstream clear)")
    return 0


def cmd_isolate(args: argparse.Namespace) -> int:
    from gridmend.flisr.isolate import IsolateError, faulted_zone, isolate
    from gridmend.flisr.locate import LocateError, locate_fault
    from gridmend.flisr.trace import incident_trace
    from gridmend.grid.load import LIVE_GRAPH

    g = db.connect().select_graph(LIVE_GRAPH)
    try:
        if faulted_zone(g) is None:
            locate_fault(g)
        incident_id, ops = isolate(g)
    except (LocateError, IsolateError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"incident {incident_id}: isolated Z{faulted_zone(g)}")
    for i, op in enumerate(ops, 1):
        print(f"  {i}. {op.op} {op.switch_id}")
    for plan in incident_trace(g, incident_id):
        print(f"trace: {incident_id} -> {plan['plan']} ({plan['kind']}, {plan['status']}) "
              f"-> {len(plan['ops'])} switch ops")
    return cmd_status(args)


def parse_ops(close: str, open_: str) -> list:
    from gridmend.flisr.trace import SwitchOp

    def ids(arg: str) -> list[str]:
        return [s.strip() for s in arg.split(",") if s.strip()]

    return [SwitchOp(s, "open") for s in ids(open_)] + [SwitchOp(s, "close") for s in ids(close)]


def print_verify(result) -> None:
    verdict = "PASS" if result.passed else "REJECTED"
    ops = ", ".join(f"{o.op} {o.switch_id}" for o in result.ops)
    print(f"plan {result.plan_id}: {ops} -> {verdict} (verified on fork_{result.plan_id})")
    print(f"  claims:   {zone_list(result.claims)}")
    print(f"  restores: {zone_list(result.restored_zones)} "
          f"(net {result.restored_kw:.0f} kW, weighted {result.restored_weighted:.0f})")
    if result.dropped_zones:
        print(f"  drops:    {zone_list(result.dropped_zones)} (were energized)")
    if result.max_loading_pct is not None:
        print(f"  max loading: {result.max_loading_pct:.0f}%")
    for kind, detail in result.violations:
        print(f"  violation [{kind}]: {detail}")


def cmd_verify(args: argparse.Namespace) -> int:
    from gridmend.flisr.trace import open_incident
    from gridmend.flisr.verify import next_plan_id, verify_plan
    from gridmend.grid.load import LIVE_GRAPH

    conn = db.connect()
    g = conn.select_graph(LIVE_GRAPH)
    incident = open_incident(g)
    if not incident or incident["status"] != "isolated":
        print("no isolated incident; run fault and isolate first", file=sys.stderr)
        return 1
    ops = parse_ops(args.close, args.open)
    if not ops:
        print("nothing to verify; pass --close and/or --open", file=sys.stderr)
        return 1

    result = verify_plan(conn, g, incident["id"], next_plan_id(g, incident["id"], "v"), ops)
    print_verify(result)
    return 0 if result.passed else 2


def cmd_reset(args: argparse.Namespace) -> int:
    from gridmend.sim import reset

    reset(db.connect())
    print("grid_live reset from grid_base")
    return 0


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

    fault = sub.add_parser("fault", help="simulate a fault on an unswitched segment")
    fault.add_argument("line")
    fault.set_defaults(func=cmd_fault)

    sub.add_parser("reset", help="restore grid_live from grid_base").set_defaults(func=cmd_reset)
    sub.add_parser("locate", help="locate the faulted zone from indicators").set_defaults(
        func=cmd_locate
    )
    sub.add_parser("isolate", help="isolate the faulted zone and record the incident").set_defaults(
        func=cmd_isolate
    )

    verify = sub.add_parser("verify", help="verify a switching plan on a forked graph")
    verify.add_argument("--close", default="", help="comma-separated switch ids to close")
    verify.add_argument("--open", default="", help="comma-separated switch ids to open")
    verify.set_defaults(func=cmd_verify)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))
