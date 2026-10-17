"""pandapower's mv_oberrhein network mapped onto GridMend's model: a real 20 kV network with two
110/20 kV substations, four feeders and six normally open points."""
from __future__ import annotations

import copy
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pandapower.auxiliary import pandapowerNet


@cache
def _base_network() -> pandapowerNet:
    import pandapower.networks as pn

    net = pn.mv_oberrhein()
    # Normally open points become out-of-service lines, the way case33 stores its ties, and every
    # line switch is closed, so a line conducts exactly when it is in service.
    open_lines = set(net.switch[(net.switch.et == "l") & ~net.switch.closed].element)
    net.line["in_service"] = ~net.line.index.isin(open_lines)
    net.switch["closed"] = True
    return net


def load_network() -> pandapowerNet:
    return copy.deepcopy(_base_network())


@dataclass(frozen=True)
class Layout:
    sources: list[int]               # substation busbars (pandapower indices)
    bus_id: dict[int, int]           # pandapower index -> GridMend id
    line_id: dict[int, str]          # pandapower line index -> "upstream-downstream"
    parent: dict[int, int]           # bus -> upstream bus in the normal radial configuration
    feeder: dict[int, int]           # bus -> the first bus of its feeder
    children: dict[int, list[int]]


@cache
def layout() -> Layout:
    net = _base_network()
    sources = sorted(int(b) for b in net.trafo.lv_bus)
    adjacent = defaultdict(list)
    for line in net.line.itertuples():
        if line.in_service:
            adjacent[line.from_bus].append((line.to_bus, line.Index))
            adjacent[line.to_bus].append((line.from_bus, line.Index))

    # Breadth first from both substations at once, so ids grow outward and an upstream bus
    # always has a lower id than the buses it feeds.
    parent, parent_line, order = {}, {}, []
    queue, seen = deque(sources), set(sources)
    while queue:
        bus = queue.popleft()
        order.append(bus)
        for neighbour, line in sorted(adjacent[bus]):
            if neighbour not in seen:
                seen.add(neighbour)
                parent[neighbour], parent_line[neighbour] = bus, line
                queue.append(neighbour)
    bus_id = {bus: i for i, bus in enumerate(order, 1)}

    children = defaultdict(list)
    for bus, up in parent.items():
        children[up].append(bus)
    feeder = {}
    for bus in order:
        if bus in parent:
            feeder[bus] = bus if parent[bus] in sources else feeder[parent[bus]]

    line_id = {line: f"{bus_id[parent[bus]]}-{bus_id[bus]}" for bus, line in parent_line.items()}
    for line in net.line.itertuples():
        if line.Index not in line_id:
            a, b = sorted((bus_id[line.from_bus], bus_id[line.to_bus]))
            line_id[line.Index] = f"{a}-{b}"
    return Layout(sources, bus_id, line_id, parent, feeder, dict(children))


def line_ids(net) -> list[str]:
    return [layout().line_id[i] for i in net.line.index]


def bus_map(net) -> dict[int, int]:
    return dict(layout().bus_id)


def sectionalizer_buses(target: int) -> set[int]:
    """Buses whose upstream line gets a sectionalizer: walking up each feeder, a switch is placed
    as soon as the buses gathered below reach the target, so zones hold about that many."""
    lay = layout()
    gathered: dict[int, int] = {}
    cut = set()
    for bus in sorted(lay.parent, key=lambda b: lay.bus_id[b], reverse=True):
        size = 1 + sum(gathered[child] for child in lay.children.get(bus, []))
        if size >= target and lay.parent[bus] not in lay.sources:
            cut.add(bus)
            size = 0
        gathered[bus] = size
    return cut


def rows(net, assumptions: dict) -> tuple[list[dict], list[dict], list[dict]]:
    lay = layout()
    p_kw, q_kvar = defaultdict(float), defaultdict(float)
    for load in net.load.itertuples():
        p_kw[load.bus] += load.p_mw * load.scaling * 1000
        q_kvar[load.bus] += load.q_mvar * load.scaling * 1000

    # The largest load on each feeder stands in for a critical site.
    feeders = sorted(set(lay.feeder.values()), key=lambda b: lay.bus_id[b])
    critical = {}
    for site, head in zip(assumptions["critical_sites"], feeders):
        members = [b for b, f in lay.feeder.items() if f == head]
        critical[max(members, key=lambda b: (p_kw[b], -lay.bus_id[b]))] = site

    weights = assumptions["weights"]
    buses = [{
        "id": lay.bus_id[bus],
        "pp_index": bus,
        "p_kw": p_kw[bus],
        "q_kvar": q_kvar[bus],
        "s_kva": math.hypot(p_kw[bus], q_kvar[bus]),
        "critical": bus in critical,
        "facility": critical.get(bus, ""),
        "weight": weights["critical"] if bus in critical else weights["default"],
        "is_source": bus in lay.sources,
        "dg_kw": 0.0,
        "dg_online": False,
    } for bus in lay.bus_id]

    sectionalized = sectionalizer_buses(assumptions["zone_buses"])
    vn_kv = net.bus.vn_kv
    segs, switches = [], []
    for line in net.line.itertuples():
        lid = lay.line_id[line.Index]
        a, b = (int(x) for x in lid.split("-"))
        row = {
            "id": lid, "a": a, "b": b,
            "r_ohm": line.r_ohm_per_km * line.length_km,
            "x_ohm": line.x_ohm_per_km * line.length_km,
            # Thermal rating of the cable: sqrt(3) x voltage x current limit.
            "cap_kva": math.sqrt(3) * vn_kv[line.from_bus] * line.max_i_ka * 1000,
        }
        downstream = next((bus for bus in (line.from_bus, line.to_bus)
                           if lay.parent.get(bus) in (line.from_bus, line.to_bus)), None)
        if not line.in_service:
            kind = "tie"
        elif lay.parent[downstream] in lay.sources:
            kind = "breaker"
        elif downstream in sectionalized:
            kind = "sectionalizer"
        else:
            segs.append(row)
            continue
        status = "open" if kind == "tie" else "closed"
        switches.append({**row, "kind": kind, "status": status, "normally": status,
                         "fi": "none" if kind == "tie" else "clear"})
    return buses, segs, switches
