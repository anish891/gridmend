from mcp.server.mcpserver import MCPServer

from gridmend import db
from gridmend.agent.tools import SCHEMAS, GridTools
from gridmend.grid.feeder import live_graph

DESCRIPTIONS = {s["function"]["name"]: s["function"]["description"] for s in SCHEMAS}

server = MCPServer(
    "gridmend",
    instructions="FLISR tools for a distribution feeder twin held in FalkorDB. Read the fault "
                 "indicators in zone_map, declare the faulted zones, then design isolation and "
                 "restoration switching yourself: simulate each plan on a forked graph, and "
                 "execute only plans that passed. The host's tool approval is the operator's "
                 "approval.",
)

_tools: GridTools | None = None


def tools() -> GridTools:
    # Execution approval comes from the MCP host confirming the tool call.
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


@server.tool(description=DESCRIPTIONS["zone_map"])
def zone_map() -> dict:
    return tools().call("zone_map", {})


@server.tool(description=DESCRIPTIONS["transfer_capacity"])
def transfer_capacity() -> dict:
    return tools().call("transfer_capacity", {})


@server.tool(description=DESCRIPTIONS["declare_fault"])
def declare_fault(zones: list[int], evidence: str) -> dict:
    return tools().call("declare_fault", {"zones": zones, "evidence": evidence})


@server.tool(description=DESCRIPTIONS["simulate_plan"])
def simulate_plan(purpose: str, close: list[str] | None = None,
                  open: list[str] | None = None) -> dict:
    return tools().call("simulate_plan", {"purpose": purpose, "close": close, "open": open})


@server.tool(description=DESCRIPTIONS["execute_isolation"])
def execute_isolation(plan_id: str) -> dict:
    return tools().call("execute_isolation", {"plan_id": plan_id})


@server.tool(description=DESCRIPTIONS["recommend_plan"])
def recommend_plan(plan_id: str, reason: str) -> dict:
    return tools().call("recommend_plan", {"plan_id": plan_id, "reason": reason})


@server.tool(description=DESCRIPTIONS["execute_restoration"])
def execute_restoration(plan_id: str) -> dict:
    return tools().call("execute_restoration", {"plan_id": plan_id})


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
