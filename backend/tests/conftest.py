from types import SimpleNamespace

import pytest

from gridmend import db
from gridmend.grid.case33 import load_network
from gridmend.grid.load import BASE_GRAPH, line_rows, load_assumptions, write_graph

TEST_GRAPH = "grid_test"


def segment_ids() -> list[str]:
    segs, _ = line_rows(load_network(), load_assumptions())
    return [s["id"] for s in segs]


@pytest.fixture(scope="session")
def falkordb():
    conn = db.connect()
    if BASE_GRAPH not in conn.list_graphs():
        write_graph(conn)
    return conn


@pytest.fixture
def graph(falkordb):
    # Each test gets its own copy of the pristine grid so grid_live is never touched.
    if TEST_GRAPH in falkordb.list_graphs():
        falkordb.select_graph(TEST_GRAPH).delete()
    g = falkordb.select_graph(BASE_GRAPH).copy(TEST_GRAPH)
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
