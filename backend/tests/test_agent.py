import json
from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from gridmend.agent.llm import parse_json_object
from gridmend.agent.loop import run_agent
from gridmend.agent.tools import GridTools
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault


class ScriptedModel:
    """Stands in for the LLM: follows the system prompt's procedure using real tool results."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, model, messages, tools, temperature):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        step = len(results)
        script = ["grid_status", "locate_fault", "isolation_plan", "restoration_candidates"]
        if step < len(script):
            return self.reply(script[step], {})
        candidates = results[3]["candidates"]
        verified = results[4:4 + len(candidates)]
        if len(verified) < len(candidates):
            return self.reply("verify_plan", {"rank": len(verified) + 1})
        passing = [v for v in verified if v["passed"]]
        best = max(passing, key=lambda v: (len(v["critical_loads_restored"]), v["restored_kw"]))
        if step == 4 + len(candidates):
            return self.reply("execute_plan", {"plan_id": best["plan_id"]})
        if step == 5 + len(candidates):
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
    assert [name for name, _ in steps][:4] == [
        "grid_status", "locate_fault", "isolation_plan", "restoration_candidates"]
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


def test_tools_reject_invented_switches_and_unknown_tools(falkordb, graph):
    apply_fault(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: True)
    tools.locate_fault()
    tools.isolation_plan()
    assert "unknown switches" in tools.call("verify_plan", {"close": ["7-99"]})["error"]
    assert "unknown tool" in tools.call("run_cypher", {})["error"]


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
