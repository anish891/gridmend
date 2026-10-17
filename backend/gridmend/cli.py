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
    from gridmend.sim import FaultError, apply_faults

    try:
        path = apply_faults(db.connect().select_graph(LIVE_GRAPH), args.lines)
    except FaultError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"fault on {', '.join(args.lines)}; breaker tripped")
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
    for zone in location.zones:
        print(f"faulted zone: Z{zone}")
        print(f"evidence:     {' -> '.join(location.chains[zone])} (all set; downstream clear)")
    return 0


def cmd_isolate(args: argparse.Namespace) -> int:
    from gridmend.flisr.isolate import IsolateError, faulted_zones, secure
    from gridmend.flisr.locate import LocateError
    from gridmend.flisr.trace import incident_trace
    from gridmend.grid.load import LIVE_GRAPH

    g = db.connect().select_graph(LIVE_GRAPH)
    try:
        incident_id, ops = secure(g)
    except (LocateError, IsolateError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"incident {incident_id}: isolated {', '.join(f'Z{z}' for z in faulted_zones(g))}")
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
    if result.min_v_pu is not None:
        print(f"  min voltage: {result.min_v_pu:.3f} pu")
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


def cmd_plan(args: argparse.Namespace) -> int:
    from gridmend.flisr.candidates import plan_restoration
    from gridmend.flisr.trace import open_incident
    from gridmend.grid.load import LIVE_GRAPH

    conn = db.connect()
    g = conn.select_graph(LIVE_GRAPH)
    incident = open_incident(g)
    if not incident or incident["status"] != "isolated":
        print("no isolated incident; run fault and isolate first", file=sys.stderr)
        return 1

    verified = plan_restoration(conn, g, incident["id"])
    if not verified:
        print("no restoration candidates: no dead zone can be reached from a live one")
        return 0
    for rank, (candidate, result) in enumerate(verified, 1):
        print(f"#{rank} [{candidate.origin}] score {candidate.score:.0f}, "
              f"claims {zone_list(list(candidate.claims))} ({candidate.claimed_kw:.0f} kW)")
        print_verify(result)
        print()
    return 0


def print_explanation(e: dict) -> None:
    print(f"incident {e['incident']}: fault on {e['fault_line']} in "
          f"{', '.join(f'Z{z}' for z in e['faulted_zones'])} "
          f"({e['status']})")
    print(f"isolation: {', '.join(e['isolation'])}")
    plan = e["plan"]
    if plan:
        print(f"executed {plan['id']}: {', '.join(plan['ops'])}")
        print(f"  restored {plan['restored_kw']:.0f} kW; margins: min voltage "
              f"{plan['min_v_pu']:.3f} pu, max loading {plan['max_loading_pct']:.0f}%")
        for zone, path in plan["paths"].items():
            print(f"  Z{zone} fed via {' -> '.join(path)}")
    else:
        print("no restoration plan executed")
    t = e["transfer"]
    if t["shortfall_kva"]:
        print(f"transfer limit (max flow): {t['deliverable_kva']} of {t['demand_kva']} kVA of "
              f"healthy load can be carried at most; limited by {', '.join(t['bottlenecks'])}")
    print("critical loads:")
    for c in e["critical"]:
        state = "restored" if c["energized"] else "dark"
        line = f"  {c['facility']} (bus {c['bus']}, Z{c['zone']}): {state}"
        print(line + (f" - {c['reason']}" if c["reason"] else ""))
    if e["rejected"]:
        print("rejected alternatives:")
        for r in e["rejected"]:
            print(f"  {r['id']} ({', '.join(r['ops'])}): {'; '.join(r['violations'])}")
    print("switching order:")
    for s in e["switching_order"]:
        print(f"  {s['step']}. {s['op']} {s['switch_kind']} {s['switch_id']}  ({s['phase']}, {s['plan']})")


def cmd_run(args: argparse.Namespace) -> int:
    from gridmend.flisr.execute import restore
    from gridmend.flisr.explain import explain_incident
    from gridmend.flisr.isolate import isolate
    from gridmend.flisr.locate import locate_fault
    from gridmend.flisr.trace import open_incident
    from gridmend.grid.load import LIVE_GRAPH
    from gridmend.sim import FaultError, apply_faults

    conn = db.connect()
    g = conn.select_graph(LIVE_GRAPH)
    try:
        apply_faults(g, args.lines)
    except FaultError as exc:
        print(exc, file=sys.stderr)
        return 1
    # Each round isolates the faults the indicators can see; reclosing onto a hidden one trips
    # the breaker again and starts the next round.
    while True:
        location = locate_fault(g)
        for zone in location.zones:
            print(f"located Z{zone} from indicators {' -> '.join(location.chains[zone])}")
        incident_id, ops = isolate(g)
        print(f"isolated: {', '.join(f'{o.op} {o.switch_id}' for o in ops)}")
        if open_incident(g)["status"] != "tripped":
            break
        print("the breaker tripped again: another fault was hidden upstream")

    def approve(plan: dict) -> bool:
        critical = ", ".join(plan["critical"]) or "no critical loads"
        print(f"best verified plan {plan['id']}: {plan['restored_kw']:.0f} kW, {critical}, "
              f"{plan['switch_ops']} switch ops")
        if args.auto:
            return True
        return input("execute it? [y/N] ").strip().lower() == "y"

    verified, executed = restore(conn, g, incident_id, approve)
    passed = sum(r.passed for _, r in verified)
    print(f"{len(verified)} candidates verified on forks, {passed} passed")
    print()
    print_explanation(explain_incident(g, incident_id))
    return 0


def summarize_step(name: str, result: dict) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if name == "locate_fault":
        return f"Z{result['faulted_zone']} via {' -> '.join(result['indicator_chain'])}"
    if name == "isolation_plan":
        return ", ".join(result["executed"])
    if name == "restoration_candidates":
        return f"{len(result['candidates'])} candidates"
    if name == "transfer_capacity":
        return f"{result['deliverable_kva']} of {result['demand_kva']} kVA deliverable"
    if name == "verify_plan":
        verdict = "PASS" if result["passed"] else "REJECTED " + "; ".join(
            v.split(":")[0] for v in result["violations"])
        return f"{result['plan_id']} {verdict}, {result['restored_kw']} kW"
    if name == "execute_plan":
        return "executed" if result["executed"] else result["reason"]
    return "ok"


def cmd_agent(args: argparse.Namespace) -> int:
    from gridmend.agent.llm import make_client
    from gridmend.agent.loop import run_agent
    from gridmend.agent.tools import GridTools
    from gridmend.grid.load import LIVE_GRAPH
    from gridmend.sim import FaultError, apply_fault

    conn = db.connect()
    g = conn.select_graph(LIVE_GRAPH)
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
        markdown, record_llm_only, run_all, run_llm_only, summarize, with_recorded_llm_only,
        write_outputs,
    )

    extra = []
    if args.llm_only:
        from gridmend.agent.llm import make_client

        client, model = make_client()
        extra.append(lambda conn, line: run_llm_only(conn, line, client, model))
    rows = run_all(db.connect(), extra)
    if args.llm_only:
        record_llm_only(rows)
    else:
        rows = with_recorded_llm_only(rows)
    summary = summarize(rows)
    out = write_outputs(rows, summary)
    print(markdown(rows, summary))
    print(f"wrote {out / 'results.csv'} and {out / 'results.md'}")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    from gridmend.flisr.policy import PolicyError, clear_conditions, current_context, set_conditions
    from gridmend.grid.load import LIVE_GRAPH

    g = db.connect().select_graph(LIVE_GRAPH)
    try:
        if args.clear:
            context = clear_conditions(g)
        elif args.conditions:
            from gridmend.agent.llm import make_client

            client, model = make_client()
            context = set_conditions(g, client, model, args.conditions)
        else:
            context = current_context(g)
    except PolicyError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"conditions: {context['conditions'] or 'normal day'}")
    for p in context["priorities"]:
        change = "" if p["weight"] == p["standing"] else f" (standing {p['standing']})"
        print(f"  {p['facility']} (bus {p['bus']}, Z{p['zone']}): {p['weight']}{change}  {p['rule']}")
    return 0


def cmd_crew(args: argparse.Namespace) -> int:
    from gridmend.flisr.crew import CrewError, crew_reports, file_report, jammed_switches
    from gridmend.grid.load import LIVE_GRAPH

    g = db.connect().select_graph(LIVE_GRAPH)
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


def cmd_whatif(args: argparse.Namespace) -> int:
    from gridmend.flisr.isolate import IsolateError
    from gridmend.flisr.locate import LocateError
    from gridmend.flisr.whatif import WhatIfError, what_if
    from gridmend.grid.load import LIVE_GRAPH
    from gridmend.sim import FaultError

    conn = db.connect()
    try:
        r = what_if(conn, conn.select_graph(LIVE_GRAPH), args.switches, args.fault)
    except (WhatIfError, FaultError, LocateError, IsolateError) as exc:
        print(exc, file=sys.stderr)
        return 1
    for label, o in (("as things are", r["as_is"]), (f"if {', '.join(r['switches'])} cannot operate", r["jammed"])):
        print(f"{label}:")
        print(f"  isolation: {', '.join(o['isolation'])}")
        for c in o["cut_off"]:
            print(f"  Z{c['zone']} cut out with the fault ({c['via']} jammed)")
        plan = o["plan"]
        print(f"  best safe plan: {', '.join(plan['ops'])}, {plan['restored_kw']} kW" if plan
              else "  no safe restoration plan")
        print(f"  critical sites restored: {', '.join(o['critical_restored']) or 'none'}")
    print(f"difference: {r['kw_change']:+} kW"
          + (f"; loses {', '.join(r['critical_lost'])}" if r["critical_lost"] else "")
          + (f"; gains {', '.join(r['critical_gained'])}" if r["critical_gained"] else ""))
    return 0


def cmd_dg(args: argparse.Namespace) -> int:
    from gridmend.flisr.generation import generation_status, reconnect_generation
    from gridmend.grid.load import LIVE_GRAPH

    g = db.connect().select_graph(LIVE_GRAPH)
    if args.reconnect:
        result = reconnect_generation(g)
        back = ", ".join(f"bus {b}" for b in result["reconnected"]) or "nothing"
        print(f"reconnected: {back}")
        if result["replan"]:
            print("healthy zones are still dark: planning is open again (gridmend plan)")
    for unit in generation_status(g):
        state = "online" if unit["online"] else "offline"
        power = "has power" if unit["energized"] else "no power"
        print(f"bus {unit['bus']} (Z{unit['zone']}): {unit['kw']:.0f} kW, {state}, bus {power}")
    return 0


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

    fault = sub.add_parser("fault", help="simulate faults on one or more unswitched segments")
    fault.add_argument("lines", nargs="+", metavar="line")
    fault.set_defaults(func=cmd_fault)

    sub.add_parser("reset", help="restore grid_live from grid_base").set_defaults(func=cmd_reset)
    dg = sub.add_parser("dg", help="distributed generation: status, or reconnect units with power")
    dg.add_argument("--reconnect", action="store_true",
                    help="bring back units whose bus has power again (after the reconnection delay)")
    dg.set_defaults(func=cmd_dg)

    whatif = sub.add_parser("whatif", help="compare planning with and without switches that "
                                           "cannot operate, on copies of the grid")
    whatif.add_argument("switches", nargs="+", help="switch ids that cannot be operated")
    whatif.add_argument("--fault", help="segment to fault first, when the feeder is healthy")
    whatif.set_defaults(func=cmd_whatif)

    crew = sub.add_parser("crew", help="file a field crew's note; the LLM reads which switches "
                                       "are jammed or repaired")
    crew.add_argument("note", nargs="?", help="the crew's note as written; omit to list reports")
    crew.set_defaults(func=cmd_crew)

    context = sub.add_parser("context", help="set today's conditions; the LLM applies the "
                                              "restoration policy to them")
    context.add_argument("conditions", nargs="?", help="the operator's report, in plain words")
    context.add_argument("--clear", action="store_true", help="back to standing priorities")
    context.set_defaults(func=cmd_context)
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

    sub.add_parser("plan", help="generate, rank and verify restoration candidates").set_defaults(
        func=cmd_plan
    )

    run = sub.add_parser("run", help="fault one or more lines and run the full FLISR loop")
    run.add_argument("lines", nargs="+", metavar="line")
    run.add_argument("--auto", action="store_true", help="execute the best plan without asking")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser("eval", help="run all single faults against the baselines")
    evaluate.add_argument("--llm-only", action="store_true",
                          help="also run the tool-less LLM baseline")
    evaluate.set_defaults(func=cmd_eval)

    agent = sub.add_parser("agent", help="fault a line and let the LLM operator restore it")
    agent.add_argument("line")
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
