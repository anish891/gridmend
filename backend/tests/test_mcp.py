import asyncio

from gridmend.agent.mcp_server import server
from gridmend.agent.tools import SCHEMAS


def test_mcp_exposes_exactly_the_agent_tools():
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {s["function"]["name"] for s in SCHEMAS}
