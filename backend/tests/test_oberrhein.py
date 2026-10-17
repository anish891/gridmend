from collections import Counter

import yaml

from gridmend.grid import oberrhein
from gridmend.grid.feeder import DATA


def model():
    with open(DATA / "oberrhein.yaml") as f:
        assumptions = yaml.safe_load(f)
    return oberrhein.rows(oberrhein.load_network(), assumptions)


def test_both_substations_and_their_four_feeders_are_modelled():
    buses, segs, switches = model()
    assert len(buses) == 177
    assert [b["id"] for b in buses if b["is_source"]] == [1, 2]
    kinds = Counter(s["kind"] for s in switches)
    assert kinds == {"breaker": 4, "sectionalizer": 16, "tie": 6}
    assert len(segs) + len(switches) == 181


def test_line_ids_name_the_upstream_bus_first():
    _, segs, switches = model()
    for row in segs + [s for s in switches if s["kind"] != "tie"]:
        assert row["a"] < row["b"]


def test_one_critical_site_per_feeder_and_real_cable_ratings():
    buses, segs, _ = model()
    assert sorted(b["facility"] for b in buses if b["critical"]) == [
        "fire station", "hospital", "telecom", "water pumping"]
    # sqrt(3) x 20 kV x 0.362 kA, the smallest cable in the network.
    assert min(round(s["cap_kva"]) for s in segs) == 12540


def test_power_flow_ids_match_the_graph():
    net = oberrhein.load_network()
    assert len(set(oberrhein.line_ids(net))) == len(net.line)
    assert sorted(oberrhein.bus_map(net).values()) == list(range(1, 178))
