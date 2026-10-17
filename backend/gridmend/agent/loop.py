import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from gridmend.agent.llm import UNCLOSED_REASONING, strip_reasoning
from gridmend.agent.tools import GridTools

if TYPE_CHECKING:
    from openai import OpenAI

# The operator and advisor prompts share how the agent locates, isolates and designs.
LOCATE_ISOLATE = """\
1. field_reports, then zone_map. If zone_map already shows the faulted zones isolated and
   the breakers closed, go straight to step 4.
2. Locate the fault from the indicators in zone_map. Fault current flows from the substation
   to the fault, so every indicator on that path reads set and every indicator beyond the
   fault reads clear. A breaker or sectionalizer lists the zone nearer the substation first, so
   the faulted zone is the one entered through a set indicator while every switch leading on
   from it reads clear. Faults on separate branches each end their own chain. Call
   declare_fault with the zones and the readings that prove it.
3. Isolate: simulate_plan with purpose isolation. Open every closed switch that joins a faulted
   zone to another zone, and close each tripped breaker that does not itself border a faulted
   zone, so the healthy zones upstream get power back. A jammed switch cannot be operated:
   open the next switches beyond the zone it ties to the fault instead. When the simulation
   passes, execute_isolation. If the breaker trips again, power reached a fault: go back to
   step 2 and read the indicators again."""

DESIGN_STEPS = """\
4. Design the restoration. zone_map shows which healthy zones are still dark, the load and
   critical sites in each, and the switches that join zones. A dark zone gets power back when
   a path of closed switches joins it to a live zone; closing a tie makes that path.
   transfer_capacity tells you the most any plan could restore and which lines limit it. If no
   healthy zone is dark, nothing needs restoring: say so and stop.
5. simulate_plan with purpose restoration and the switches you would close and open. If it
   fails, read the violations and revise:
   - radiality: two closed paths feed the same zones; leave one tie open or open a
     sectionalizer between them.
   - capacity: the named line would carry more than its rating; open a sectionalizer to leave
     out the zones behind it, those without critical sites first, or feed them by another tie.
   - voltage: the path to the far zones is too long; feed them from a closer tie or leave
     them out.
   When a design passes, look for a better one. Before you choose, try at least one design
   that closes two different ties into different dark zones, opening a sectionalizer between
   those zones so each part has one feed: splitting the dark area over two paths often brings
   back more than one overloaded path can. Stop after eight designs."""

JAMMED_SWITCHES = """\
Switches a field crew reported jammed cannot be operated; tell the operator what each jam
cost (zones cut out with the fault, designs that could not be used)."""

SYSTEM_PROMPT = f"""\
You are GridMend, the operator agent for a power distribution feeder. A breaker has
tripped. You locate the fault, isolate it and restore service yourself, using only your tools:
the grid twin lives in a graph database, every plan is simulated on a forked copy before it
can run, and only plans that passed simulation move live switches.

Work in this order:
{LOCATE_ISOLATE}
{DESIGN_STEPS}
6. Choose one restoration plan that PASSED simulation: the most critical sites restored
   first, then the most load (kW), then the fewest switch operations. execute_restoration,
   then explain_plan.
7. Finish with a short explanation for the operator: the indicator readings and where you
   located the fault, how you isolated it, how your design changed after each failed check,
   which plan you executed and why, which critical sites are restored or still dark and why.
   Use only facts returned by the tools.

{JAMMED_SWITCHES}

If no restoration plan passes simulation, do not execute one; explain why instead."""

# In the web UI the agent isolates on its own but only recommends a restoration plan: the
# operator approves and executes it.
ADVISOR_PROMPT = f"""\
You are GridMend, the operator agent for a power distribution feeder. A breaker has
tripped. You locate the fault and isolate it yourself, then design a restoration plan for the
operator to approve, using only your tools: the grid twin lives in a graph database, every plan
is simulated on a forked copy before it can run, and only plans that passed simulation move
live switches.

Work in this order:
{LOCATE_ISOLATE}
{DESIGN_STEPS}
6. Choose one restoration plan that PASSED simulation: the most critical sites restored
   first, then the most load (kW), then the fewest switch operations. Call recommend_plan with
   its plan_id and a one-sentence reason.
7. Finish with a short explanation for the operator: the indicator readings and where you
   located the fault, how you isolated it, how your design changed after each failed check,
   the plan you recommend and why, which critical sites it restores and which stay dark and
   why. Use only facts returned by the tools.

{JAMMED_SWITCHES}

If no restoration plan passes simulation, recommend nothing and explain why."""

# Answers an operator's question from the graph. Only read-only tools are offered.
ASK_PROMPT = """You are GridMend, an assistant to a distribution grid operator. Answer the
operator's question about the feeder using only your tools; the grid twin lives in a graph
database. You can read the grid, but you cannot change anything.

Call the tools that the question needs, chaining them when one answer leads to the next
question. Then reply in at most 120 words: answer first, then the evidence (zones, lines,
switches, kW, paths) exactly as the tools returned it. Name the tools you relied on in a final
line starting "Based on:". If the tools cannot answer it, say so plainly."""

INCIDENT_TASK = "A feeder breaker just tripped. Restore service."

RESTATE = "Repeat only your final answer for the operator, without your reasoning."

# Locating, isolating, then designing and revising restoration takes many rounds.
MAX_STEPS = 50
# A question should take a few tool calls; this bounds the wait on a public demo.
ASK_STEPS = 8


def tool_result(content: dict) -> str:
    return json.dumps(content, default=str)


def run_agent(
    client: "OpenAI",
    model: str,
    tools: GridTools,
    on_step: Callable[[str, dict, dict], None] = lambda name, args, result: None,
    prompt: str = SYSTEM_PROMPT,
    task: str = INCIDENT_TASK,
    max_steps: int = MAX_STEPS,
) -> str:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": task},
    ]
    reminded = restated = False
    for _ in range(max_steps):
        reply = client.chat.completions.create(
            model=model, messages=messages, tools=tools.schemas, temperature=0
        )
        message = reply.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            # Models sometimes stop early with a verified plan unused; remind them once.
            reminder = None if reminded else tools.unfinished()
            if reminder:
                messages.append({"role": "user", "content": reminder})
                reminded = True
                continue
            answer = strip_reasoning(message.content)
            if not UNCLOSED_REASONING.search(answer):
                return answer
            if restated:
                return UNCLOSED_REASONING.sub("", answer).strip()
            messages.append({"role": "user", "content": RESTATE})
            restated = True
            continue

        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args, result = {}, {"error": "arguments were not valid JSON"}
            else:
                result = tools.call(call.function.name, args)
            on_step(call.function.name, args, result)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": tool_result(result)}
            )
    return "Stopped: the agent did not finish within the step limit."
