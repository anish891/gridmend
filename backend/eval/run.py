import csv
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from falkordb import FalkorDB, Graph

from gridmend.flisr.candidates import cached_screen, greedy_candidate, read_situation, save_screens
from gridmend.flisr.execute import restore
from gridmend.flisr.isolate import secure
from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import verify_plan
from gridmend.grid import feeder
from gridmend.grid.case33 import save_voltages
from gridmend.grid.load import BASE_GRAPH
from gridmend.sim import apply_fault

if TYPE_CHECKING:
    from eval.optimal import Feeder

EVAL_GRAPH = "grid_eval"
# The IEEE 33-bus results stay where the API and image expect them; other feeders get a subfolder.
OUT_DIR = Path(__file__).resolve().parent / "out" / ("" if feeder.NAME == "ieee33" else feeder.NAME)
# The LLM-only baseline needs a model endpoint and is not deterministic, so `eval --llm-only`
# records its rows here and later runs, including the image build, replay them.
RECORDED_LLM = Path(__file__).resolve().parent / "llm_only.csv"


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
    g = db.select_graph(BASE_GRAPH).copy(EVAL_GRAPH)
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
    result = verify_plan(db, g, incident_id, f"{incident_id}-{method}", ops)
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
    candidate = greedy_candidate(cached_screen(g), read_situation(g))
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


def run_gridmend(db: FalkorDB, oracle: "Feeder | None", line: str) -> Row:
    g, incident_id = isolated_graph(db, line)
    start = time.perf_counter()
    _, executed = restore(db, g, incident_id, approve=lambda plan: True)
    plan_ms = 1000 * (time.perf_counter() - start)
    unserved_kw, critical_dark = left_dark(g)
    if executed is None:
        return Row(line, "gridmend", 0.0, 0, 0, 0, plan_ms,
                   unserved_kw=unserved_kw, critical_dark=critical_dark)

    # Re-check the executed configuration with the independent oracle, where there is one; the
    # oracle handles one substation, so a two-substation feeder relies on the fork checks alone.
    violations = 0
    if oracle is not None:
        closed = frozenset(row[0] for row in g.ro_query(
            "MATCH ()-[s:SW {status: 'closed'}]->() RETURN s.id"
        ).result_set)
        violations = len(oracle.evaluate(closed, oracle.isolated_state(line)[0]).violations)
    return Row(line, "gridmend", executed["restored_kw"], len(executed["critical"]),
               executed["switch_ops"], violations, plan_ms,
               unserved_kw=unserved_kw, critical_dark=critical_dark)


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

    if feeder.NAME == "ieee33":
        oracle = Feeder()
        segments = oracle.segments
    else:
        oracle = None
        segments = [row[0] for row in db.select_graph(BASE_GRAPH).ro_query(
            "MATCH ()-[s:SEG]->() RETURN s.id ORDER BY s.id"
        ).result_set]
    rows = []
    for line in segments:
        optimal = run_optimal(oracle, line) if oracle else None
        scenario = [Row(line, "isolate-only", 0.0, 0, 0, 0, 0.0), run_greedy(db, line)]
        scenario += [method(db, line) for method in extra_methods]
        scenario += [run_gridmend(db, oracle, line)] + ([optimal] if optimal else [])
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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    (OUT_DIR / "results.md").write_text(markdown(rows, summary))
    # Every fault's candidate screens and verified power flows were just computed; ship them so a
    # deployed API starts warm.
    save_screens(OUT_DIR / "screens.json")
    save_voltages(OUT_DIR / "voltages.json")
    return OUT_DIR


def load_results() -> list[Row] | None:
    return read_rows(OUT_DIR / "results.csv")


def record_llm_only(rows: list[Row]) -> None:
    with open(RECORDED_LLM, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows if r.method == "llm-only")


def with_recorded_llm_only(rows: list[Row]) -> list[Row]:
    """Insert the recorded LLM-only rows after each fault's greedy row."""
    recorded = {r.fault: r for r in read_rows(RECORDED_LLM) or []}
    merged = []
    for row in rows:
        merged.append(row)
        if row.method == "greedy" and row.fault in recorded:
            merged.append(recorded[row.fault])
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
