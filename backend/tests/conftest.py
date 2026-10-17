from types import SimpleNamespace

import pytest

from gridmend import db
from gridmend.flisr.execute import execute_plan
from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import next_plan_id, verify_plan
from gridmend.grid.case33 import load_network
from gridmend.grid.feeder import base_graph
from gridmend.grid.load import line_rows, load_assumptions, write_graph
from gridmend.sim import apply_fault

TEST_GRAPH = "grid_test"


def segment_ids() -> list[str]:
    segs, _ = line_rows(load_network(), load_assumptions())
    return [s["id"] for s in segs]


def isolated(graph, line: str) -> str:
    """The feeder after the standard isolation, for tests of what comes after it."""
    from eval.isolation import secure

    apply_fault(graph, line)
    incident_id, _ = secure(graph)
    return incident_id


def run_plan(falkordb, graph, incident_id: str, close=(), open=(), kind="restoration"):
    """Simulate a plan as the agent would, and execute it if it passed."""
    ops = [SwitchOp(s, "open") for s in open] + [SwitchOp(s, "close") for s in close]
    result = verify_plan(falkordb, graph, incident_id, next_plan_id(graph, incident_id, "t"),
                         ops, origin="agent", kind=kind)
    if result.passed:
        execute_plan(graph, result.plan_id)
    return result


@pytest.fixture(scope="session")
def falkordb():
    conn = db.connect()
    if base_graph() not in conn.list_graphs():
        write_graph(conn)
    return conn


@pytest.fixture
def graph(falkordb):
    # Each test gets its own copy of the pristine grid so grid_live is never touched.
    if TEST_GRAPH in falkordb.list_graphs():
        falkordb.select_graph(TEST_GRAPH).delete()
    g = falkordb.select_graph(base_graph()).copy(TEST_GRAPH)
    yield g
    g.delete()


class FixedModel:
    """Stands in for the LLM with one canned reply, keeping the prompt it was sent."""

    def __init__(self, reply: str):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.reply = reply
        self.prompt = ""

    def create(self, **kwargs):
        self.prompt = kwargs["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


@pytest.fixture
def fixed_model():
    return FixedModel
