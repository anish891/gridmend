"""Brute-force restoration oracle, built from the case data with networkx and pandapower only.

It never reads FalkorDB, so it also serves as an independent check of GridMend's results.
"""
from dataclasses import dataclass
from itertools import product

import networkx as nx
import pandapower as pp

from gridmend.grid.case33 import bus_voltages, load_network
from gridmend.grid.load import bus_rows, line_rows, load_assumptions


@dataclass(frozen=True)
class Outcome:
    closed: frozenset[str]
    energized_zones: frozenset[int]
    violations: tuple[str, ...]


@dataclass(frozen=True)
class Best:
    switch_ops: int
    restored_kw: float
    critical: tuple[str, ...]
    closed: frozenset[str]


class Feeder:
    def __init__(self):
        net = load_network()
        assumptions = load_assumptions()
        self.voltage_min = assumptions["voltage_min_pu"]
        self.source = assumptions["substation"]
        self.buses = {b["id"]: b for b in bus_rows(net, assumptions)}
        segs, switches = line_rows(net, assumptions)
        self.edges = {row["id"]: row for row in segs + switches}
        self.switches = {row["id"]: row for row in switches}
        self.segments = [row["id"] for row in segs]

        seg_graph = nx.Graph()
        seg_graph.add_nodes_from(self.buses)
        seg_graph.add_edges_from((self.edges[s]["a"], self.edges[s]["b"]) for s in self.segments)
        self.zone_of = {}
        for component in nx.connected_components(seg_graph):
            if self.source not in component:
                for bus in component:
                    self.zone_of[bus] = min(component)

    def zone_kw(self, zones) -> float:
        return sum(b["p_kw"] for b in self.buses.values() if self.zone_of.get(b["id"]) in zones)

    def critical_in(self, zones) -> tuple[str, ...]:
        return tuple(b["facility"] for b in self.buses.values()
                     if b["critical"] and self.zone_of.get(b["id"]) in zones)

    def isolated_state(self, fault_line: str) -> tuple[int, frozenset[str], list[str]]:
        """Faulted zone, switches closed after isolation, and the switches left to decide."""
        faulted = self.zone_of[self.edges[fault_line]["a"]]
        boundary = {
            s for s, row in self.switches.items()
            if (self.zone_of.get(row["a"]) == faulted) != (self.zone_of.get(row["b"]) == faulted)
        }
        closed = frozenset(s for s, row in self.switches.items()
                           if row["status"] == "closed" and s not in boundary)
        decision = sorted(s for s, row in self.switches.items()
                          if s not in boundary and row["kind"] != "breaker")
        return faulted, closed, decision

    def evaluate(self, closed: frozenset[str], faulted: int, voltage: bool = True) -> Outcome:
        g = nx.Graph()
        g.add_node(self.source)
        for edge_id, row in self.edges.items():
            if edge_id in self.segments or edge_id in closed:
                g.add_edge(row["a"], row["b"], id=edge_id)
        energized = nx.node_connected_component(g, self.source)
        zones = frozenset(self.zone_of[b] for b in energized if b in self.zone_of)

        violations = []
        if faulted in zones:
            violations.append("isolation")
        tree = g.subgraph(energized)
        if tree.number_of_edges() != len(energized) - 1:
            violations.append("radiality")
        else:
            violations += self.capacity_violations(tree)
            if voltage and not violations:
                violations += self.voltage_violations(tree)
        return Outcome(closed, zones - {faulted}, tuple(violations))

    def capacity_violations(self, tree: nx.Graph) -> list[str]:
        p = {b: self.buses[b]["p_kw"] for b in tree}
        q = {b: self.buses[b]["q_kvar"] for b in tree}
        # Accumulate load from the leaves up towards the source.
        violations = []
        for up, bus in reversed(list(nx.bfs_edges(tree, self.source))):
            row = self.edges[tree.edges[up, bus]["id"]]
            if (p[bus] ** 2 + q[bus] ** 2) ** 0.5 > row["cap_kva"]:
                violations.append("capacity")
            p[up] += p[bus]
            q[up] += q[bus]
        return violations[:1]

    def voltage_violations(self, tree: nx.Graph) -> list[str]:
        conducting = {data["id"] for _, _, data in tree.edges(data=True)}
        try:
            voltages = bus_voltages(conducting)
        except pp.LoadflowNotConverged:
            return ["voltage"]
        return ["voltage"] if min(voltages.values()) < self.voltage_min else []


def optimal_restoration(feeder: Feeder, fault_line: str) -> Best:
    """Same objective as GridMend: critical loads first, then net kW restored, then fewest ops."""
    faulted, isolated, decision = feeder.isolated_state(fault_line)
    live = feeder.evaluate(isolated, faulted, voltage=False).energized_zones

    ranked = []
    for states in product((False, True), repeat=len(decision)):
        closed = (isolated - set(decision)) | {s for s, on in zip(decision, states) if on}
        outcome = feeder.evaluate(frozenset(closed), faulted, voltage=False)
        if outcome.violations:
            continue
        gained, lost = outcome.energized_zones - live, live - outcome.energized_zones
        critical = len(feeder.critical_in(gained)) - len(feeder.critical_in(lost))
        kw = feeder.zone_kw(gained) - feeder.zone_kw(lost)
        ops = len(closed ^ isolated)
        ranked.append(((-critical, -round(kw, 6), ops), closed, gained, kw, ops))

    # Power flows are the expensive part, so only run them from the best down.
    for _, closed, gained, kw, ops in sorted(ranked, key=lambda r: r[0]):
        if not feeder.evaluate(closed, faulted).violations:
            return Best(ops, kw, feeder.critical_in(gained), closed)
    return Best(0, 0.0, (), isolated)
