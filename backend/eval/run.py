import csv
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import openai
from falkordb import FalkorDB, Graph

from eval.greedy import greedy_candidate
from eval.isolation import secure
from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import verify_plan
from gridmend.grid import feeder
from gridmend.grid.case33 import save_voltages
from gridmend.sim import apply_fault

if TYPE_CHECKING:
    from eval.optimal import Feeder

EVAL_GRAPH = "grid_eval"
OUT_ROOT = Path(__file__).resolve().parent / "out"


def out_dir() -> Path:
    # The IEEE 33-bus results stay where they always were; other feeders get a subfolder.
    return OUT_ROOT if feeder.name() == "ieee33" else OUT_ROOT / feeder.name()
# Model-driven methods need an endpoint and are not deterministic, so a run with a model records
# their rows here and later runs, including the image build, replay them.
RECORDED = {"llm-only": Path(__file__).resolve().parent / "llm_only.csv",
            "agent": Path(__file__).resolve().parent / "agent.csv"}


@dataclass
class Row:
    fault: str
    method: str
    restored_kw: float
    critical: int
    switch_ops: int
    violations: int
    plan_ms: float
    optimal_kw: float = 0.0
    # After the method's switching: load still dark and critical facilities still dark.
    unserved_kw: float = 0.0
    critical_dark: str = ""
    # What unverified switching would have restored had it been safe; shown next to the 0 it scores.
    claimed_kw: float = 0.0


def isolated_graph(db: FalkorDB, line: str) -> tuple[Graph, str]:
    if EVAL_GRAPH in db.list_graphs():
        db.select_graph(EVAL_GRAPH).delete()
    g = db.select_graph(feeder.base_graph()).copy(EVAL_GRAPH)
    apply_fault(g, line)
    incident_id, _ = secure(g)
    return g, incident_id


def critical_count(g: Graph, zones: list[int]) -> int:
    return g.ro_query(
        "MATCH (b:Bus {critical: true}) WHERE b.zone IN $zones RETURN count(b)", {"zones": zones}
    ).result_set[0][0]


def score_unverified(db: FalkorDB, g: Graph, incident_id: str, line: str, method: str,
                     ops: list[SwitchOp], plan_ms: float) -> Row:
    """Switching chosen without verification is executed as-is; the verifier only counts
    what it would have caught. Unsafe switching restores nothing: protection would trip it."""
    if not ops:
        return Row(line, method, 0.0, 0, 0, 0, plan_ms)
    result = verify_plan(db, g, incident_id, f"{incident_id}-{method}", ops, origin=method)
    safe = result.passed
    return Row(
        fault=line,
        method=method,
        restored_kw=result.restored_kw if safe else 0.0,
        critical=critical_count(g, result.restored_zones) if safe else 0,
        switch_ops=len(ops),
        violations=len(result.violations),
        plan_ms=plan_ms,
        claimed_kw=result.restored_kw,
    )


def run_greedy(db: FalkorDB, line: str) -> Row:
    g, incident_id = isolated_graph(db, line)
    start = time.perf_counter()
    candidate = greedy_candidate(g)
    plan_ms = 1000 * (time.perf_counter() - start)
    ops = list(candidate.ops) if candidate else []
    return score_unverified(db, g, incident_id, line, "greedy", ops, plan_ms)


def describe_feeder(g: Graph) -> str:
    """The text the tool-less LLM baseline gets: the isolated feeder, as an operator would see it."""
    buses = g.ro_query(
        "MATCH (b:Bus) RETURN b.id, b.zone, b.p_kw, b.q_kvar, b.facility ORDER BY b.id"
    ).result_set
    lines = g.ro_query(
        """
        MATCH (a:Bus)-[r:SEG|SW]->(b:Bus)
        RETURN r.id, CASE type(r) WHEN 'SEG' THEN 'segment' ELSE r.kind END,
               coalesce(r.status, 'closed'), r.r_ohm, r.x_ohm, r.cap_kva
        ORDER BY a.id, b.id
        """
    ).result_set
    zones = g.ro_query(
        "MATCH (z:Zone) RETURN z.id, z.energized, z.faulted ORDER BY z.id"
    ).result_set

    out = ["Substation at bus 1, 12.66 kV nominal. Zones are named by their lowest bus.",
           "", "Zones:"]
    for zone, energized, faulted in zones:
        state = "FAULTED (isolated)" if faulted else "energized" if energized else "dark"
        out.append(f"  Z{zone}: {state}")
    out += ["", "Buses (id, zone, P kW, Q kVAr, critical facility):"]
    for bus, zone, p_kw, q_kvar, facility in buses:
        zone_name = f"Z{zone}" if zone is not None else "substation"
        out.append(f"  {bus}, {zone_name}, {p_kw:.0f}, {q_kvar:.0f}, {facility or '-'}")
    out += ["", "Lines (id, type, status, R ohm, X ohm, rating kVA); only switches can be "
                "operated:"]
    for line_id, kind, status, r_ohm, x_ohm, cap in lines:
        out.append(f"  {line_id}, {kind}, {status}, {r_ohm:.3f}, {x_ohm:.3f}, {cap:.0f}")
    return "\n".join(out)


def run_llm_only(db: FalkorDB, line: str, client, model: str) -> Row:
    from gridmend.agent.llm import llm_only_switching
    from gridmend.grid.load import load_assumptions

    g, incident_id = isolated_graph(db, line)
    start = time.perf_counter()
    try:
        plan = llm_only_switching(client, model, describe_feeder(g),
                                  load_assumptions()["voltage_min_pu"])
    except ValueError:
        # No usable plan in the reply: an operator could not act on it, so it counts as a failure.
        return Row(line, "llm-only", 0.0, 0, 0, 1, 1000 * (time.perf_counter() - start))
    plan_ms = 1000 * (time.perf_counter() - start)

    # Switch ids the model invented cannot be operated; each one counts as a violation.
    known = {row[0] for row in g.ro_query("MATCH ()-[s:SW]->() RETURN s.id").result_set}
    ops = ([SwitchOp(s, "open") for s in plan["open"] if s in known]
           + [SwitchOp(s, "close") for s in plan["close"] if s in known])
    invalid = sum(s not in known for s in plan["open"] + plan["close"])
    row = score_unverified(db, g, incident_id, line, "llm-only", ops, plan_ms)
    if invalid:
        row.violations += invalid
        row.restored_kw, row.critical = 0.0, 0
    return row


def served(g: Graph) -> tuple[float, int]:
    kw, critical = g.ro_query(
        "MATCH (b:Bus {energized: true}) RETURN sum(b.p_kw), sum(CASE WHEN b.critical THEN 1 ELSE 0 END)"
    ).result_set[0]
    return kw or 0.0, critical


def run_agent_row(db: FalkorDB, line: str, client, model: str) -> Row:
    """The LLM agent handles the fault end to end: it reads the indicators, isolates and
    restores on its own. It is scored against the same isolated feeder as every other method,
    so load it brings back counts whether its isolation or its restoration returned it."""
    from gridmend.agent.loop import run_agent
    from gridmend.agent.tools import GridTools
    from gridmend.flisr.trace import open_incident

    baseline, _ = isolated_graph(db, line)
    isolated_kw, isolated_critical = served(baseline)
    baseline.delete()

    g = db.select_graph(feeder.base_graph()).copy(EVAL_GRAPH)
    apply_fault(g, line)
    start = time.perf_counter()
    try:
        run_agent(client, model, GridTools(db, g, approve=lambda plan: True))
    except openai.APIError:
        # A provider hiccup is not the agent's mistake: start the fault over once.
        g.delete()
        g = db.select_graph(feeder.base_graph()).copy(EVAL_GRAPH)
        apply_fault(g, line)
        start = time.perf_counter()
        run_agent(client, model, GridTools(db, g, approve=lambda plan: True))
    plan_ms = 1000 * (time.perf_counter() - start)
    unserved_kw, critical_dark = left_dark(g)
    incident = open_incident(g)
    if incident is None or incident["status"] not in ("isolated", "restored"):
        # The fault was never isolated, so the breaker stays open: safe, but nothing is restored.
        return Row(line, "agent", 0.0, 0, 0, 0, plan_ms,
                   unserved_kw=unserved_kw, critical_dark=critical_dark)

    kw, critical = served(g)
    ops = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration', status: 'executed'})
        RETURN sum(p.switch_ops)
        """,
        {"id": incident["id"]},
    ).result_set[0][0] or 0
    # FalkorDB returns sums as floats; the counts are whole numbers.
    return Row(line, "agent", max(kw - isolated_kw, 0.0), int(max(critical - isolated_critical, 0)),
               int(ops), len(oracle_violations(g, line)), plan_ms,
               unserved_kw=unserved_kw, critical_dark=critical_dark)


def oracle_violations(g: Graph, line: str) -> tuple[str, ...]:
    # The independent oracle re-checks the final configuration where there is one; it handles
    # one substation, so a two-substation feeder relies on the fork checks alone.
    if feeder.name() != "ieee33":
        return ()
    from eval.optimal import Feeder

    oracle = Feeder()
    closed = frozenset(row[0] for row in g.ro_query(
        "MATCH ()-[s:SW {status: 'closed'}]->() RETURN s.id"
    ).result_set)
    return oracle.evaluate(closed, oracle.isolated_state(line)[0]).violations


def left_dark(g: Graph) -> tuple[float, str]:
    """Load and critical facilities still without power, for the risk map."""
    kw, facilities = g.ro_query(
        """
        MATCH (b:Bus {energized: false})
        RETURN sum(b.p_kw), collect(CASE WHEN b.critical THEN b.facility END)
        """
    ).result_set[0]
    return kw or 0.0, ";".join(facilities)


def run_optimal(feeder: "Feeder", line: str) -> Row:
    from eval.optimal import optimal_restoration

    start = time.perf_counter()
    best = optimal_restoration(feeder, line)
    plan_ms = 1000 * (time.perf_counter() - start)
    return Row(line, "optimal", best.restored_kw, len(best.critical), best.switch_ops, 0, plan_ms)


def run_all(db: FalkorDB, extra_methods=()) -> list[Row]:
    # The oracle needs networkx and pandapower; the API only reads results, so import it here.
    from eval.optimal import Feeder

    if feeder.name() == "ieee33":
        oracle = Feeder()
        segments = oracle.segments
    else:
        oracle = None
        segments = [row[0] for row in db.select_graph(feeder.base_graph()).ro_query(
            "MATCH ()-[s:SEG]->() RETURN s.id ORDER BY s.id"
        ).result_set]
    rows = []
    for line in segments:
        optimal = run_optimal(oracle, line) if oracle else None
        scenario = [Row(line, "isolate-only", 0.0, 0, 0, 0, 0.0), run_greedy(db, line)]
        scenario += [method(db, line) for method in extra_methods]
        scenario += [optimal] if optimal else []
        for row in scenario:
            row.optimal_kw = optimal.restored_kw if optimal else 0.0
        rows += scenario
    if EVAL_GRAPH in db.list_graphs():
        db.select_graph(EVAL_GRAPH).delete()
    return rows


def summarize(rows: list[Row]) -> list[dict]:
    methods = list(dict.fromkeys(r.method for r in rows))
    optimal_total = sum(r.restored_kw for r in rows if r.method == "optimal")
    summary = []
    for method in methods:
        mine = [r for r in rows if r.method == method]
        restored = sum(r.restored_kw for r in mine)
        summary.append({
            "method": method,
            "restored_kw": restored,
            "pct_optimal": 100 * restored / optimal_total if optimal_total else 0.0,
            "critical": sum(r.critical for r in mine),
            "switch_ops": sum(r.switch_ops for r in mine),
            "violations": sum(r.violations for r in mine),
            "unsafe_plans": sum(r.violations > 0 for r in mine),
            "mean_plan_ms": sum(r.plan_ms for r in mine) / len(mine),
        })
    return summary


def markdown(rows: list[Row], summary: list[dict]) -> str:
    lines = [
        "| method | restored kW | % of optimal | critical loads | switch ops | violations "
        "| unsafe plans | mean planning ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summary:
        lines.append(
            f"| {s['method']} | {s['restored_kw']:.0f} | {s['pct_optimal']:.1f} | {s['critical']} "
            f"| {s['switch_ops']} | {s['violations']} | {s['unsafe_plans']} | {s['mean_plan_ms']:.0f} |"
        )

    methods = [s["method"] for s in summary]
    lines += ["", "Restored kW per fault (violations in brackets):", "",
              "| fault | " + " | ".join(methods) + " |",
              "|---|" + "---:|" * len(methods)]
    for fault in dict.fromkeys(r.fault for r in rows):
        cells = []
        for method in methods:
            r = next(r for r in rows if r.fault == fault and r.method == method)
            cells.append(f"{r.restored_kw:.0f}" + (f" [{r.violations}]" if r.violations else ""))
        lines.append(f"| {fault} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_outputs(rows: list[Row], summary: list[dict]) -> Path:
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    (out / "results.md").write_text(markdown(rows, summary))
    # Every power flow the evaluation ran ships with the results, so a deployed API starts warm.
    save_voltages(out / "voltages.json")
    return out


def load_results() -> list[Row] | None:
    return read_rows(out_dir() / "results.csv")


def record_rows(rows: list[Row], methods: list[str]) -> None:
    for method in methods:
        with open(RECORDED[method], "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
            writer.writeheader()
            writer.writerows(asdict(r) for r in rows if r.method == method)


def with_recorded_rows(rows: list[Row], methods: list[str]) -> list[Row]:
    """Insert recorded model-driven rows after each fault's greedy row, before optimal."""
    recorded = {m: {r.fault: r for r in read_rows(RECORDED[m]) or []} for m in methods}
    merged = []
    for row in rows:
        merged.append(row)
        if row.method == "greedy":
            merged += [recorded[m][row.fault] for m in methods if row.fault in recorded[m]]
    return merged


def read_rows(path: Path) -> list[Row] | None:
    if not path.exists():
        return None
    with open(path, newline="") as f:
        return [
            Row(
                fault=r["fault"],
                method=r["method"],
                restored_kw=float(r["restored_kw"]),
                critical=int(r["critical"]),
                switch_ops=int(r["switch_ops"]),
                violations=int(r["violations"]),
                plan_ms=float(r["plan_ms"]),
                optimal_kw=float(r["optimal_kw"]),
                unserved_kw=float(r.get("unserved_kw") or 0.0),
                critical_dark=r.get("critical_dark") or "",
                claimed_kw=float(r.get("claimed_kw") or 0.0),
            )
            for r in csv.DictReader(f)
        ]
