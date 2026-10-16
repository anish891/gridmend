import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from gridmend.agent.tools import GridTools

if TYPE_CHECKING:
    from openai import OpenAI

SYSTEM_PROMPT = """You are GridMend, an assistant to a distribution grid operator. The feeder's
breaker has tripped. Restore service safely using only your tools; the grid twin lives in a
graph database and every switching action goes through deterministic, verified code.

Work in this order:
1. operating_context, grid_status, then locate_fault, then isolation_plan.
2. restoration_candidates, then verify_plan for each candidate worth considering (by rank).
   transfer_capacity tells you how much load any plan could restore at most and which lines
   limit it.
3. Choose one plan that PASSED verification, following the restoration policy: the highest
   total priority weight of critical sites restored first, then the most load (kW), then the
   fewest switch operations. Among plans that restore the same critical sites, use the
   operating conditions (crew arrival, weather) as policy section 4 says. Never pick a plan
   that failed.
4. execute_plan with that plan_id, then explain_plan.
5. Finish with a short explanation for the operator: the conditions and priorities in force,
   what faulted, what was isolated, which plan you executed and why (cite policy rule
   numbers), which critical sites are restored or still dark and why, and why the main
   alternatives were rejected. Use only facts returned by the tools.

Switches a field crew reported jammed cannot be operated. Isolation and the candidates already
work around them; tell the operator what each jam cost (zones cut out with the fault, plans
that could not be used). If a crew report or the conditions cast doubt on a switch your
chosen plan needs, what_if shows what losing it would cost without touching the live grid.

If no candidate passes verification, do not execute anything; explain why instead."""

# In the web UI the agent advises and the operator executes, so it never gets execute_plan.
ADVISOR_PROMPT = """You are GridMend, an assistant to a distribution grid operator. The feeder's
breaker has tripped. Work the incident using only your tools; the grid twin lives in a graph
database and every action goes through deterministic, verified code. You advise; the operator
approves and executes.

Work in this order:
1. operating_context, then locate_fault, then isolation_plan.
2. restoration_candidates, then verify_plan for each candidate (by rank).
   transfer_capacity tells you how much load any plan could restore at most and which lines
   limit it.
3. Choose one plan that PASSED verification, following the restoration policy: the highest
   total priority weight of critical sites restored first, then the most load (kW), then the
   fewest switch operations. Among plans that restore the same critical sites, use the
   operating conditions (crew arrival, weather) as policy section 4 says. Never choose a plan
   that failed. Call recommend_plan with its plan_id and a one-sentence reason that cites the
   policy rule numbers.
4. Finish with a short explanation for the operator: the conditions and priorities in force,
   what faulted and what was isolated, the plan you recommend and why, which critical sites it
   restores and which stay dark and why, and why the main alternatives were rejected. Use only
   facts returned by the tools.

Switches a field crew reported jammed cannot be operated. Isolation and the candidates already
work around them; tell the operator what each jam cost (zones cut out with the fault, plans
that could not be used). If a crew report or the conditions cast doubt on a switch your
chosen plan needs, what_if shows what losing it would cost without touching the live grid.

If no candidate passes verification, recommend nothing and explain why."""

MAX_STEPS = 25


def tool_result(content: dict) -> str:
    return json.dumps(content, default=str)


def run_agent(
    client: "OpenAI",
    model: str,
    tools: GridTools,
    on_step: Callable[[str, dict, dict], None] = lambda name, args, result: None,
    prompt: str = SYSTEM_PROMPT,
) -> str:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "The feeder breaker just tripped. Restore service."},
    ]
    for _ in range(MAX_STEPS):
        reply = client.chat.completions.create(
            model=model, messages=messages, tools=tools.schemas, temperature=0
        )
        message = reply.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return message.content or ""

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
