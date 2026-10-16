from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.transfer import transfer_limit
from gridmend.sim import apply_fault


def test_healthy_feeder_can_carry_all_load(falkordb, graph):
    t = transfer_limit(graph)
    assert t["shortfall_kva"] == 0
    assert t["bottlenecks"] == []
    assert not [name for name in falkordb.list_graphs() if name.startswith("transfer_")]


def test_z3_fault_is_limited_by_lateral_2_19(graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    isolate(graph)
    t = transfer_limit(graph)
    assert t["bottlenecks"] == ["2-19"]
    assert t["deliverable_kva"] < t["demand_kva"]
