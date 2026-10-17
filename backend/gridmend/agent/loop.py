import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from gridmend.agent.llm import UNCLOSED_REASONING, strip_reasoning
from gridmend.agent.tools import GridTools

if TYPE_CHECKING:
    from openai import OpenAI

# Both incident prompts share how the agent designs and revises restoration plans.
DESIGN_STEPS = """2. Design the restoration yourself. zone_map shows which healthy zones are
   dark, the load and critical sites in each, and the switches that join zones. A dark zone
   gets power back when a path of closed switches joins it to a live zone; closing a tie makes
   that path. transfer_capacity tells you the most any plan could restore and which lines
   limit it. If no healthy zone is dark, nothing needs restoring: say so and stop.
3. verify_plan with the switches you would close and open. It checks your design on a forked
   copy of the grid. If it fails, read the violations and revise:
   - radiality: two closed paths feed the same zones; leave one tie open or open a
     sectionalizer between them.
   - capacity: the named line would carry more than its rating; open a sectionalizer to leave
     out the zones behind it, those without critical sites first, or feed them by another tie.
   - voltage: the path to the far zones is too long; feed them from a closer tie or leave
     them out.
   Revise until a design passes or you have tried five designs.
4. restoration_candidates, then verify_plan by rank for any generated plan that might beat
   your best design. They check your work; they are not a starting point."""

JAMMED_SWITCHES = """Switches a field crew reported jammed cannot be operated. Isolation and
the candidates already work around them; tell the operator what each jam cost (zones cut out
with the fault, plans that could not be used). If a crew report casts doubt on a switch your
chosen plan needs, what_if shows what losing it would cost without touching the live grid."""

SYSTEM_PROMPT = f"""You are GridMend, an assistant to a distribution grid operator. The feeder's
breaker has tripped. Restore service safely using only your tools; the grid twin lives in a
graph database and every switching action goes through deterministic, verified code.

Work in this order:
1. field_reports, grid_status, then locate_fault, then isolation_plan. If the breaker trips
   again, locate_fault and isolation_plan once more: a second fault was hidden upstream.
{DESIGN_STEPS}
5. Choose one plan that PASSED verification, yours or generated: the most critical sites
   restored first, then the most load (kW), then the fewest switch operations. Never pick a
   plan that failed.
6. execute_plan with that plan_id, then explain_plan.
7. Finish with a short explanation for the operator: what faulted, what was isolated, how your
   design changed after each failed check, which plan you executed and why, which critical
   sites are restored or still dark and why, and why the main alternatives were rejected. Use
   only facts returned by the tools.

{JAMMED_SWITCHES}

If no plan passes verification, do not execute anything; explain why instead."""

# In the web UI the agent advises and the operator executes, so it never gets execute_plan.
ADVISOR_PROMPT = f"""You are GridMend, an assistant to a distribution grid operator. The feeder's
breaker has tripped. Work the incident using only your tools; the grid twin lives in a graph
database and every action goes through deterministic, verified code. You advise; the operator
approves and executes.

Work in this order:
1. field_reports, then locate_fault, then isolation_plan. If the breaker trips again,
   locate_fault and isolation_plan once more: a second fault was hidden upstream.
{DESIGN_STEPS}
5. Choose one plan that PASSED verification, yours or generated: the most critical sites
   restored first, then the most load (kW), then the fewest switch operations. Never choose a
   plan that failed. Call recommend_plan with its plan_id and a one-sentence reason.
6. Finish with a short explanation for the operator: what faulted and what was isolated, how
   your design changed after each failed check, the plan you recommend and why, which critical
   sites it restores and which stay dark and why, and why the main alternatives were rejected.
   Use only facts returned by the tools.

{JAMMED_SWITCHES}

If no plan passes verification, recommend nothing and explain why."""

# Answers an operator's question from the graph. Only read-only tools are offered.
ASK_PROMPT = """You are GridMend, an assistant to a distribution grid operator. Answer the
operator's question about the feeder using only your tools; the grid twin lives in a graph
database. You can read the grid and plan on throwaway copies, but you cannot change anything.

Call the tools that the question needs, chaining them when one answer leads to the next
question. Then reply in at most 120 words: answer first, then the evidence (zones, lines,
switches, kW, paths) exactly as the tools returned it. Name the tools you relied on in a final
line starting "Based on:". If the tools cannot answer it, say so plainly."""

INCIDENT_TASK = "The feeder breaker just tripped. Restore service."

RESTATE = "Repeat only your final answer for the operator, without your reasoning."

# Designing, revising and cross-checking take more rounds than picking from a list.
MAX_STEPS = 40
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
