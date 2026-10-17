import json
from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from gridmend.agent.llm import parse_json_object
from gridmend.agent.loop import run_agent
from gridmend.agent.tools import GridTools
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault


# A first design that overloads 2-19, then the revision that leaves Z10 and Z14 out.
DESIGNS = [{"close": ["8-21"]}, {"close": ["8-21"], "open": ["9-10"]}]


class ScriptedModel:
    """Stands in for the LLM: follows the system prompt's procedure using real tool results."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, model, messages, tools, temperature):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        step = len(results)
        script = ["grid_status", "locate_fault", "isolation_plan", "zone_map"]
        if step < len(script):
            return self.reply(script[step], {})
        designs = results[len(script):len(script) + len(DESIGNS)]
        if len(designs) < len(DESIGNS):
            return self.reply("verify_plan", DESIGNS[len(designs)])
        listed = len(script) + len(DESIGNS)
        if step == listed:
            return self.reply("restoration_candidates", {})
        candidates = results[listed]["candidates"]
        checked = results[listed + 1:listed + 1 + len(candidates)]
        if len(checked) < len(candidates):
            return self.reply("verify_plan", {"rank": len(checked) + 1})
        passing = [v for v in designs + checked if v["passed"]]
        best = max(passing, key=lambda v: (len(v["critical_loads_restored"]), v["restored_kw"]))
        done = listed + 1 + len(candidates)
        if step == done:
            return self.reply("execute_plan", {"plan_id": best["plan_id"]})
        if step == done + 1:
            return self.reply("explain_plan", {})
        return self.answer(f"Executed {best['plan_id']}.")

    def reply(self, name, args):
        self.calls += 1
        call = ChatCompletionMessageToolCall(
            id=f"call{self.calls}", type="function",
            function=Function(name=name, arguments=json.dumps(args)),
        )
        return self.wrap(ChatCompletionMessage(role="assistant", tool_calls=[call]))

    def answer(self, text):
        return self.wrap(ChatCompletionMessage(role="assistant", content=text))

    @staticmethod
    def wrap(message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_agent_restores_hospital_and_fire_station_through_tools(falkordb, graph):
    apply_fault(graph, "4-5")
    steps = []
    tools = GridTools(falkordb, graph, approve=lambda plan: True)

    answer = run_agent(ScriptedModel(), "scripted", tools,
                       on_step=lambda name, args, result: steps.append((name, result)))

    assert answer.startswith("Executed")
    assert [name for name, _ in steps][:7] == [
        "grid_status", "locate_fault", "isolation_plan", "zone_map",
        "verify_plan", "verify_plan", "restoration_candidates"]
    first, revised = steps[4][1], steps[5][1]
    assert first["designed_by"] == "agent" and not first["passed"]
    assert any(v.startswith("capacity: 2-19") for v in first["violations"])
    assert revised["passed"] and revised["critical_loads_restored"] == ["hospital"]
    assert grid_status(graph)["energized_zones"] == [2, 7, 14, 19]
    explanation = steps[-1][1]
    assert {c["facility"] for c in explanation["critical"] if c["energized"]} == {
        "hospital", "fire station"}


def test_tools_refuse_to_execute_rejected_plans(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    tools.locate_fault()
    tools.isolation_plan()
    rejected = tools.verify_plan(close=["8-21", "12-22"])
    assert not rejected["passed"]

    result = tools.call("execute_plan", {"plan_id": rejected["plan_id"]})
    assert "error" in result
    assert grid_status(graph)["energized_zones"] == [2, 19]


class UnclosedReasoningModel:
    """Answers once with a reasoning block it never closes, then cleanly when asked again."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.asked = []

    def create(self, model, messages, tools, temperature):
        self.asked.append(messages[-1]["content"])
        text = ("<reasoning>No dark zones, so nothing to do.**Summary** Nothing to restore."
                if len(self.asked) == 1 else "**Summary** Nothing to restore.")
        return ScriptedModel.wrap(ChatCompletionMessage(role="assistant", content=text))


def test_an_unclosed_reasoning_block_is_restated_without_it(falkordb, graph):
    model = UnclosedReasoningModel()
    answer = run_agent(model, "scripted", GridTools(falkordb, graph, approve=lambda plan: True))
    assert answer == "**Summary** Nothing to restore."
    assert "without your reasoning" in model.asked[1]


class EarlyStopModel:
    """Verifies a passing design, then stops without executing until reminded."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.reminders = []

    def create(self, model, messages, tools, temperature):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        script = [("locate_fault", {}), ("isolation_plan", {}),
                  ("verify_plan", {"close": ["8-21"], "open": ["9-10"]})]
        if len(results) < len(script):
            return ScriptedModel().reply(*script[len(results)])
        if messages[-1]["role"] == "user":
            self.reminders.append(messages[-1]["content"])
            return ScriptedModel().reply("execute_plan", {"plan_id": results[2]["plan_id"]})
        return ScriptedModel.wrap(ChatCompletionMessage(role="assistant", content="Done."))


def test_an_agent_that_stops_with_a_safe_plan_unused_is_reminded_once(falkordb, graph):
    apply_fault(graph, "4-5")
    model = EarlyStopModel()
    run_agent(model, "scripted", GridTools(falkordb, graph, approve=lambda plan: True))
    assert len(model.reminders) == 1 and "none was executed" in model.reminders[0]
    assert grid_status(graph)["energized_zones"] == [2, 7, 19]


def test_a_declined_plan_is_not_pushed_again(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: False)
    tools.locate_fault()
    tools.isolation_plan()
    passed = tools.verify_plan(close=["8-21"], open=["9-10"])
    assert tools.unfinished() is not None
    tools.call("execute_plan", {"plan_id": passed["plan_id"]})
    assert tools.unfinished() is None


def test_generated_candidates_wait_for_the_agents_own_design(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    tools.locate_fault()
    tools.isolation_plan()
    assert "design a plan" in tools.call("restoration_candidates", {})["error"]

    tools.call("verify_plan", {"close": ["8-21", "12-22"]})
    candidates = tools.call("restoration_candidates", {})["candidates"]
    assert candidates

    # Re-entering a generated plan's switches by hand does not make it the agent's design.
    first = candidates[0]
    switches = {"close": [], "open": []}
    for step in first["ops"]:
        op, switch_id = step.split()
        switches[op].append(switch_id)
    assert tools.call("verify_plan", switches)["designed_by"] == first["origin"]


def test_zone_map_shows_what_the_agent_designs_from(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    tools.locate_fault()
    tools.isolation_plan()
    view = tools.call("zone_map", {})

    states = {z["zone"]: z["state"] for z in view["zones"]}
    assert states["Z3"] == "faulted" and states["Z2"] == "live" and states["Z7"] == "dark"
    assert next(z for z in view["zones"] if z["zone"] == "Z7")["critical"] == ["hospital"]
    switches = {s["id"]: s for s in view["switches"]}
    assert switches["8-21"] == {"id": "8-21", "kind": "tie", "status": "open", "cap_kva": 1400,
                                "joins": ["Z7", "Z19"]}
    assert switches["1-2"]["joins"] == ["substation", "Z2"]


def test_tools_reject_invented_switches_and_unknown_tools(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    tools.locate_fault()
    tools.isolation_plan()
    assert "unknown switches" in tools.call("verify_plan", {"close": ["7-99"]})["error"]
    assert "unknown tool" in tools.call("run_cypher", {})["error"]
    assert "error" not in tools.call("zone_map", {"top": 0})


def test_json_is_extracted_from_chatty_replies():
    reply = 'Sure! ```json\n{"close": ["8-21"], "open": ["9-10"]}\n``` Done.'
    assert parse_json_object(reply) == {"close": ["8-21"], "open": ["9-10"]}


def test_think_blocks_are_ignored():
    reply = '<think>maybe {"close": ["9-15"]}?</think>{"close": ["8-21"], "open": []}'
    assert parse_json_object(reply) == {"close": ["8-21"], "open": []}


def test_line_dependencies_rank_lines_by_load_at_stake(falkordb, graph):
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    lines = tools.call("line_dependencies", {"top": 3})["lines"]
    assert [l["line"] for l in lines] == ["1-2", "2-3", "3-4"]
    assert lines[0]["buses"] == 32 and lines[0]["kw"] == 3715
