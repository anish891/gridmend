import json
from itertools import count
from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from gridmend.agent.llm import parse_json_object
from gridmend.agent.loop import run_agent
from gridmend.agent.tools import GridTools
from gridmend.flisr.trace import open_incident
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault

ISOLATE_Z3 = {"purpose": "isolation", "open": ["2-3", "3-23", "6-7", "6-26"], "close": ["1-2"]}
# A first design that overloads 2-19, then the revision that splits the load over two ties.
DESIGNS = [{"purpose": "restoration", "close": ["8-21"]},
           {"purpose": "restoration", "close": ["8-21", "9-15"], "open": ["9-10", "13-14"]}]


CALL_IDS = count(1)


def reply(name, args):
    call = ChatCompletionMessageToolCall(
        id=f"call{next(CALL_IDS)}", type="function",
        function=Function(name=name, arguments=json.dumps(args)),
    )
    return wrap(ChatCompletionMessage(role="assistant", tool_calls=[call]))


def answer(text):
    return wrap(ChatCompletionMessage(role="assistant", content=text))


def wrap(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def results(messages):
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


class ScriptedModel:
    """Stands in for the LLM: reads the indicators, isolates, designs and revises, using real
    tool results to fill in plan ids."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools, temperature):
        done = results(messages)
        script = [
            ("field_reports", {}), ("zone_map", {}),
            ("declare_fault", {"zones": [3], "evidence": "1-2 and 2-3 set, the rest clear"}),
            ("simulate_plan", ISOLATE_Z3),
            ("execute_isolation", lambda r: {"plan_id": r[3]["plan_id"]}),
            ("simulate_plan", DESIGNS[0]), ("simulate_plan", DESIGNS[1]),
            ("execute_restoration", lambda r: {"plan_id": r[6]["plan_id"]}),
            ("explain_plan", {}),
        ]
        if len(done) == len(script):
            return answer("Restored the hospital and the fire station.")
        name, args = script[len(done)]
        return reply(name, args(done) if callable(args) else args)


def test_the_agent_locates_isolates_and_restores_through_tools(falkordb, graph):
    apply_fault(graph, "4-5")
    steps = []
    tools = GridTools(falkordb, graph, approve=lambda plan: True)

    text = run_agent(ScriptedModel(), "scripted", tools,
                     on_step=lambda name, args, result: steps.append((name, result)))

    assert text.startswith("Restored")
    indicators = {s["id"]: s.get("indicator") for s in steps[1][1]["switches"]}
    assert indicators["1-2"] == indicators["2-3"] == "set" and indicators["6-7"] == "clear"
    assert steps[4][1]["executed"] == ["open 2-3", "open 3-23", "open 6-7", "open 6-26",
                                       "close 1-2"]
    first, revised = steps[5][1], steps[6][1]
    assert not first["passed"] and any(v.startswith("capacity: 2-19") for v in first["violations"])
    assert revised["passed"] and revised["critical_loads_restored"] == ["hospital", "fire station"]
    assert grid_status(graph)["energized_zones"] == [2, 7, 14, 19]
    assert open_incident(graph)["status"] == "restored"


def isolated_by_tools(falkordb, graph, approve=lambda plan: True) -> GridTools:
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=approve)
    tools.call("declare_fault", {"zones": ["Z3"], "evidence": "1-2 and 2-3 set"})
    iso = tools.call("simulate_plan", ISOLATE_Z3)
    tools.call("execute_isolation", {"plan_id": iso["plan_id"]})
    return tools


def test_tools_refuse_to_execute_plans_that_failed_simulation(falkordb, graph):
    tools = isolated_by_tools(falkordb, graph)
    loop = tools.call("simulate_plan", {"purpose": "restoration", "close": ["8-21", "12-22"]})
    assert not loop["passed"]

    assert "passed simulation" in tools.call("execute_restoration", {"plan_id": loop["plan_id"]})["error"]
    assert grid_status(graph)["energized_zones"] == [2, 19]


def test_an_advisor_isolates_but_cannot_restore(falkordb, graph):
    tools = GridTools(falkordb, graph, approve=lambda plan: False, can_execute=False)
    offered = {s["function"]["name"] for s in tools.schemas}
    assert "execute_isolation" in offered and "execute_restoration" not in offered


def test_plans_wait_for_the_incident_to_reach_their_stage(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    assert "declare_fault first" in tools.call("simulate_plan", ISOLATE_Z3)["error"]
    tools.call("declare_fault", {"zones": [3], "evidence": "1-2 and 2-3 set"})
    early = tools.call("simulate_plan", DESIGNS[1])
    assert "once it is isolated" in early["error"]


class UnclosedReasoningModel:
    """Answers once with a reasoning block it never closes, then cleanly when asked again."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.asked = []

    def create(self, model, messages, tools, temperature):
        self.asked.append(messages[-1]["content"])
        return answer("<reasoning>No dark zones, so nothing to do.**Summary** Nothing to restore."
                      if len(self.asked) == 1 else "**Summary** Nothing to restore.")


def test_an_unclosed_reasoning_block_is_restated_without_it(falkordb, graph):
    model = UnclosedReasoningModel()
    text = run_agent(model, "scripted", GridTools(falkordb, graph, approve=lambda plan: True))
    assert text == "**Summary** Nothing to restore."
    assert "without your reasoning" in model.asked[1]


class EarlyStopModel:
    """Isolates and finds a passing design, then stops without executing until reminded."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.reminders = []

    def create(self, model, messages, tools, temperature):
        done = results(messages)
        script = [
            ("declare_fault", {"zones": [3], "evidence": "1-2 and 2-3 set"}),
            ("simulate_plan", ISOLATE_Z3),
            ("execute_isolation", lambda r: {"plan_id": r[1]["plan_id"]}),
            ("simulate_plan", DESIGNS[1]),
        ]
        if len(done) < len(script):
            name, args = script[len(done)]
            return reply(name, args(done) if callable(args) else args)
        if messages[-1]["role"] == "user":
            self.reminders.append(messages[-1]["content"])
            return reply("execute_restoration", {"plan_id": done[3]["plan_id"]})
        return answer("Done.")


def test_an_agent_that_stops_with_a_safe_plan_unused_is_reminded_once(falkordb, graph):
    apply_fault(graph, "4-5")
    model = EarlyStopModel()
    run_agent(model, "scripted", GridTools(falkordb, graph, approve=lambda plan: True))
    assert len(model.reminders) == 1 and "none was executed" in model.reminders[0]
    assert grid_status(graph)["energized_zones"] == [2, 7, 14, 19]


def test_a_declined_plan_is_not_pushed_again(falkordb, graph):
    tools = isolated_by_tools(falkordb, graph, approve=lambda plan: False)
    passed = tools.call("simulate_plan", DESIGNS[1])
    assert tools.unfinished() is not None
    tools.call("execute_restoration", {"plan_id": passed["plan_id"]})
    assert tools.unfinished() is None


def test_an_agent_that_stops_before_isolating_is_reminded(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    assert "still tripped" in tools.unfinished()
    tools.call("declare_fault", {"zones": [3], "evidence": "1-2 and 2-3 set"})
    assert "not isolated yet" in tools.unfinished()


def test_zone_map_shows_what_the_agent_reasons_from(falkordb, graph):
    isolated_by_tools(falkordb, graph)
    view = GridTools(falkordb, graph, approve=lambda plan: True).call("zone_map", {})

    states = {z["zone"]: z["state"] for z in view["zones"]}
    assert states["Z3"] == "faulted" and states["Z2"] == "live" and states["Z7"] == "dark"
    assert next(z for z in view["zones"] if z["zone"] == "Z7")["critical"] == ["hospital"]
    switches = {s["id"]: s for s in view["switches"]}
    assert switches["8-21"] == {"id": "8-21", "kind": "tie", "status": "open", "cap_kva": 1400,
                                "joins": ["Z7", "Z19"]}
    assert switches["2-3"]["joins"] == ["Z2", "Z3"] and switches["2-3"]["indicator"] == "set"
    assert switches["1-2"]["joins"] == ["substation", "Z2"]


def test_tools_reject_invented_switches_and_unknown_tools(falkordb, graph):
    tools = isolated_by_tools(falkordb, graph)
    bad = tools.call("simulate_plan", {"purpose": "restoration", "close": ["7-99"]})
    assert "unknown switches" in bad["error"]
    assert "unknown tool" in tools.call("run_cypher", {})["error"]
    assert "error" not in tools.call("zone_map", {"top": 0})


def test_json_is_extracted_from_chatty_replies():
    text = 'Sure! ```json\n{"close": ["8-21"], "open": ["9-10"]}\n``` Done.'
    assert parse_json_object(text) == {"close": ["8-21"], "open": ["9-10"]}


def test_think_blocks_are_ignored():
    text = '<think>maybe {"close": ["9-15"]}?</think>{"close": ["8-21"], "open": []}'
    assert parse_json_object(text) == {"close": ["8-21"], "open": []}


def test_line_dependencies_rank_lines_by_load_at_stake(falkordb, graph):
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    lines = tools.call("line_dependencies", {"top": 3})["lines"]
    assert [l["line"] for l in lines] == ["1-2", "2-3", "3-4"]
    assert lines[0]["buses"] == 32 and lines[0]["kw"] == 3715
