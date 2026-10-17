import argparse
import json
import sys
from pathlib import Path

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
    from gridmend.grid.feeder import base_graph, live_graph
    from gridmend.grid.load import write_graph

    summary = write_graph(db.connect())
    sw = summary["sw"]
    print(f"wrote {base_graph()} and {live_graph()}")
    print(f"buses: {summary['buses']}")
    print(f"SEG:   {summary['seg']}")
    print(f"SW:    {sum(sw.values())} ({', '.join(f'{n} {k}' for k, n in sw.items())})")
    print(f"zones: {summary['zones']}")
    return 0


def cmd_zones(args: argparse.Namespace) -> int:
    from gridmend.grid.feeder import live_graph
    from gridmend.grid.zones import zone_table

    rows = zone_table(db.connect().select_graph(live_graph()))
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
    from gridmend.grid.feeder import live_graph

    s = grid_status(db.connect().select_graph(live_graph()))
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
    from gridmend.grid.feeder import live_graph

    status = "closed" if args.action == "close" else "open"
    if not set_switch(db.connect().select_graph(live_graph()), args.switch_id, status):
        print(f"no switch {args.switch_id}", file=sys.stderr)
        return 1
    print(f"{args.switch_id} {status}")
    return cmd_status(args)


def cmd_fault(args: argparse.Namespace) -> int:
    from gridmend.grid.feeder import live_graph
    from gridmend.sim import FaultError, apply_faults

    try:
        path = apply_faults(db.connect().select_graph(live_graph()), args.lines)
    except FaultError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"fault on {', '.join(args.lines)}; breaker tripped")
    print(f"indicators set: {', '.join(path)}")
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
    if result.min_v_pu is not None:
        print(f"  min voltage: {result.min_v_pu:.3f} pu")
    for kind, detail in result.violations:
        print(f"  violation [{kind}]: {detail}")


def cmd_verify(args: argparse.Namespace) -> int:
    from gridmend.flisr.trace import open_incident
    from gridmend.flisr.verify import next_plan_id, verify_plan
    from gridmend.grid.feeder import live_graph

    conn = db.connect()
    g = conn.select_graph(live_graph())
    incident = open_incident(g)
    if not incident or incident["status"] != "isolated":
        print("no isolated incident; let the agent isolate a fault first", file=sys.stderr)
        return 1
    ops = parse_ops(args.close, args.open)
    if not ops:
        print("nothing to verify; pass --close and/or --open", file=sys.stderr)
        return 1

    result = verify_plan(conn, g, incident["id"], next_plan_id(g, incident["id"], "v"), ops)
    print_verify(result)
    return 0 if result.passed else 2


def summarize_step(name: str, result: dict) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if name == "declare_fault":
        return f"{result['incident']}: faulted {', '.join(f'Z{z}' for z in result['faulted_zones'])}"
    if name == "transfer_capacity":
        return f"{result['deliverable_kva']} of {result['demand_kva']} kVA deliverable"
    if name == "simulate_plan":
        verdict = "PASS" if result["passed"] else "REJECTED " + "; ".join(
            v.split(":")[0] for v in result["violations"])
        return f"{result['plan_id']} {verdict}, {result['restored_kw']} kW"
    if name in ("execute_isolation", "execute_restoration"):
        if result.get("executed") is False:
            return result["reason"]
        tripped = ", BREAKER TRIPPED AGAIN" if "breaker_tripped_again" in result else ""
        return ", ".join(result["executed"]) + tripped
    return "ok"


def cmd_agent(args: argparse.Namespace) -> int:
    from gridmend.agent.llm import make_client
    from gridmend.agent.loop import run_agent
    from gridmend.agent.tools import GridTools
    from gridmend.grid.feeder import live_graph
    from gridmend.sim import FaultError, apply_fault

    conn = db.connect()
    g = conn.select_graph(live_graph())
    # Without a line the agent takes over the incident already in progress.
    if args.line:
        try:
            apply_fault(g, args.line)
        except FaultError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"fault on {args.line}; breaker tripped")

    def approve(plan: dict) -> bool:
        critical = ", ".join(plan["critical"]) or "no critical loads"
        print(f"agent asks to execute {plan['id']}: {plan['restored_kw']:.0f} kW, {critical}")
        if args.auto:
            return True
        return input("approve? [y/N] ").strip().lower() == "y"

    def on_step(name: str, call_args: dict, result: dict) -> None:
        shown = f"({json.dumps(call_args)})" if call_args else "()"
        print(f"  -> {name}{shown}: {summarize_step(name, result)}")

    client, model = make_client()
    answer = run_agent(client, model, GridTools(conn, g, approve), on_step)
    print()
    print(answer)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from gridmend.agent.mcp_server import serve

    serve()
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("gridmend.api:app", host=args.host, port=args.port)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from gridmend.site import create_site

    uvicorn.run(create_site(Path(args.ui)), host=args.host, port=args.port)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from eval.run import (
        markdown, record_rows, run_agent_row, run_all, run_llm_only, summarize,
        with_recorded_rows, write_outputs,
    )
    from gridmend.grid import feeder

    fresh = {}
    if args.llm_only or args.agent:
        from gridmend.agent.llm import make_client

        client, model = make_client()
        if args.llm_only:
            fresh["llm-only"] = lambda conn, line: run_llm_only(conn, line, client, model)
        if args.agent:
            fresh["agent"] = lambda conn, line: run_agent_row(conn, line, client, model)
    rows = run_all(db.connect(), list(fresh.values()))
    # Model-driven rows are not deterministic: a run with a model records them, and every
    # other run, including the image build, replays the recording.
    record_rows(rows, list(fresh))
    if feeder.name() == "ieee33":
        rows = with_recorded_rows(rows, [m for m in ("llm-only", "agent") if m not in fresh])
    summary = summarize(rows)
    out = write_outputs(rows, summary)
    print(markdown(rows, summary))
    print(f"wrote {out / 'results.csv'} and {out / 'results.md'}")
    return 0


def cmd_crew(args: argparse.Namespace) -> int:
    from gridmend.flisr.crew import CrewError, crew_reports, file_report, jammed_switches
    from gridmend.grid.feeder import live_graph

    g = db.connect().select_graph(live_graph())
    if args.note:
        from gridmend.agent.llm import make_client

        client, model = make_client()
        try:
            result = file_report(g, client, model, args.note)
        except CrewError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"filed {result['report']}")
        for f in result["findings"]:
            state = "works" if f["operable"] else "JAMMED"
            print(f"  {f['switch_id']}: {state}  (\"{f['evidence']}\")")
        if result["other"]:
            print(f"  also reported: {result['other']}")
        if result["rejected_plans"]:
            print(f"  plans that can no longer run: {', '.join(result['rejected_plans'])}")
        return 0

    for r in crew_reports(g):
        print(f"{r['id']}: {r['note']}")
    jammed = jammed_switches(g)
    print("jammed switches: " + (", ".join(f"{s['id']} (stuck {s['stuck']}, {s['report']})"
                                           for s in jammed) or "none"))
    return 0


def cmd_dg(args: argparse.Namespace) -> int:
    from gridmend.flisr.generation import generation_status, reconnect_generation
    from gridmend.grid.feeder import live_graph

    g = db.connect().select_graph(live_graph())
    if args.reconnect:
        result = reconnect_generation(g)
        back = ", ".join(f"bus {b}" for b in result["reconnected"]) or "nothing"
        print(f"reconnected: {back}")
        if result["replan"]:
            print("healthy zones are still dark: planning is open again (gridmend agent)")
    for unit in generation_status(g):
        state = "online" if unit["online"] else "offline"
        power = "has power" if unit["energized"] else "no power"
        print(f"bus {unit['bus']} (Z{unit['zone']}): {unit['kw']:.0f} kW, {state}, bus {power}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    from gridmend.grid.feeder import base_graph, live_graph
    from gridmend.sim import reset

    reset(db.connect())
    print(f"{live_graph()} reset from {base_graph()}")
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

    fault = sub.add_parser("fault", help="simulate faults on one or more unswitched segments")
    fault.add_argument("lines", nargs="+", metavar="line")
    fault.set_defaults(func=cmd_fault)

    sub.add_parser("reset", help="restore grid_live from grid_base").set_defaults(func=cmd_reset)
    dg = sub.add_parser("dg", help="distributed generation: status, or reconnect units with power")
    dg.add_argument("--reconnect", action="store_true",
                    help="bring back units whose bus has power again (after the reconnection delay)")
    dg.set_defaults(func=cmd_dg)

    crew = sub.add_parser("crew", help="file a field crew's note; the LLM reads which switches "
                                       "are jammed or repaired")
    crew.add_argument("note", nargs="?", help="the crew's note as written; omit to list reports")
    crew.set_defaults(func=cmd_crew)

    verify = sub.add_parser("verify", help="simulate a restoration plan on a forked graph by hand")
    verify.add_argument("--close", default="", help="comma-separated switch ids to close")
    verify.add_argument("--open", default="", help="comma-separated switch ids to open")
    verify.set_defaults(func=cmd_verify)

    evaluate = sub.add_parser("eval", help="run all single faults against the baselines")
    evaluate.add_argument("--llm-only", action="store_true",
                          help="run the tool-less LLM baseline instead of replaying it")
    evaluate.add_argument("--agent", action="store_true",
                          help="run the LLM agent on every fault instead of replaying it")
    evaluate.set_defaults(func=cmd_eval)

    agent = sub.add_parser("agent", help="fault a line; the LLM agent locates, isolates and "
                                         "restores it")
    agent.add_argument("line", nargs="?", help="segment to fault; omit to continue an incident")
    agent.add_argument("--auto", action="store_true", help="approve the agent's plan without asking")
    agent.set_defaults(func=cmd_agent)

    sub.add_parser("mcp", help="serve the agent tools over MCP (stdio)").set_defaults(
        func=cmd_mcp
    )

    api = sub.add_parser("api", help="serve the HTTP API for the UI")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.set_defaults(func=cmd_api)

    serve = sub.add_parser("serve", help="serve the API under /api and the built UI at /")
    serve.add_argument("--ui", required=True, help="directory of the exported frontend")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=7860)
    serve.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))
