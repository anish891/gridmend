from eval.greedy import greedy_candidate

from conftest import isolated


def test_greedy_closes_one_tie_into_every_dark_zone_next_to_a_live_one(graph):
    isolated(graph, "4-5")
    ops = greedy_candidate(graph).ops
    assert [(o.op, o.switch_id) for o in ops] == [("close", "12-22"), ("close", "8-21")]


def test_greedy_has_nothing_to_close_when_only_the_faulted_zone_is_dark(graph):
    # Z30 ends the 6-26 lateral, so cutting it out leaves every other zone fed.
    isolated(graph, "32-33")
    assert greedy_candidate(graph) is None
