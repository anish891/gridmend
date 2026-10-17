from mcp.server.mcpserver import MCPServer

from gridmend import db
from gridmend.agent.tools import SCHEMAS, GridTools
from gridmend.grid.feeder import live_graph

DESCRIPTIONS = {s["function"]["name"]: s["function"]["description"] for s in SCHEMAS}

server = MCPServer(
    "gridmend",
    instructions="FLISR tools for a distribution feeder twin held in FalkorDB. Locate, isolate, "
                 "generate candidates, verify each on a forked graph, then execute only a "
                 "verified plan. The host's tool approval is the operator's approval.",
)

_tools: GridTools | None = None


def tools() -> GridTools:
    # One instance per server process so restoration_candidates' ranking is remembered
    # between calls. Execution approval comes from the MCP host confirming the tool call.
    global _tools
    if _tools is None:
        conn = db.connect()
        _tools = GridTools(conn, conn.select_graph(live_graph()), approve=lambda plan: True)
    return _tools


@server.tool(description=DESCRIPTIONS["field_reports"])
def field_reports() -> dict:
    return tools().call("field_reports", {})


@server.tool(description=DESCRIPTIONS["grid_status"])
def grid_status() -> dict:
    return tools().call("grid_status", {})


@server.tool(description=DESCRIPTIONS["line_dependencies"])
def line_dependencies(top: int | None = None) -> dict:
    return tools().call("line_dependencies", {"top": top})


@server.tool(description=DESCRIPTIONS["locate_fault"])
def locate_fault() -> dict:
    return tools().call("locate_fault", {})


@server.tool(description=DESCRIPTIONS["zone_map"])
def zone_map() -> dict:
    return tools().call("zone_map", {})


@server.tool(description=DESCRIPTIONS["isolation_plan"])
def isolation_plan() -> dict:
    return tools().call("isolation_plan", {})


@server.tool(description=DESCRIPTIONS["restoration_candidates"])
def restoration_candidates() -> dict:
    return tools().call("restoration_candidates", {})


@server.tool(description=DESCRIPTIONS["transfer_capacity"])
def transfer_capacity() -> dict:
    return tools().call("transfer_capacity", {})


@server.tool(description=DESCRIPTIONS["verify_plan"])
def verify_plan(rank: int | None = None, close: list[str] | None = None,
                open: list[str] | None = None) -> dict:
    return tools().call("verify_plan", {"rank": rank, "close": close, "open": open})


@server.tool(description=DESCRIPTIONS["what_if"])
def what_if(switches: list[str], fault_line: str | None = None) -> dict:
    return tools().call("what_if", {"switches": switches, "fault_line": fault_line})


@server.tool(description=DESCRIPTIONS["recommend_plan"])
def recommend_plan(plan_id: str, reason: str) -> dict:
    return tools().call("recommend_plan", {"plan_id": plan_id, "reason": reason})


@server.tool(description=DESCRIPTIONS["execute_plan"])
def execute_plan(plan_id: str) -> dict:
    return tools().call("execute_plan", {"plan_id": plan_id})


@server.tool(description=DESCRIPTIONS["fault_impact"])
def fault_impact(line: str | None = None) -> dict:
    return tools().call("fault_impact", {"line": line})


@server.tool(description=DESCRIPTIONS["feeding_path"])
def feeding_path(bus: int | None = None, facility: str | None = None) -> dict:
    return tools().call("feeding_path", {"bus": bus, "facility": facility})


@server.tool(description=DESCRIPTIONS["explain_plan"])
def explain_plan() -> dict:
    return tools().call("explain_plan", {})


def serve() -> None:
    server.run("stdio")
