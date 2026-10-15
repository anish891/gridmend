import json
from collections.abc import Callable

from openai import OpenAI

from gridmend.agent.tools import SCHEMAS, GridTools

SYSTEM_PROMPT = """You are GridMend, an assistant to a distribution grid operator. The feeder's
breaker has tripped. Restore service safely using only your tools; the grid twin lives in a
graph database and every switching action goes through deterministic, verified code.

Work in this order:
1. grid_status, then locate_fault, then isolation_plan.
2. restoration_candidates, then verify_plan for each candidate worth considering (by rank).
3. Choose one plan that PASSED verification: restore the most critical loads first, then the
   most load (kW), then use the fewest switch operations. Never pick a plan that failed.
4. execute_plan with that plan_id, then explain_plan.
5. Finish with a short explanation for the operator: what faulted, what was isolated, which
   plan you executed and why, which critical loads are restored or still dark and why, and
   why the main alternatives were rejected. Use only facts returned by the tools.

If no candidate passes verification, do not execute anything; explain why instead."""

MAX_STEPS = 25


def tool_result(content: dict) -> str:
    return json.dumps(content, default=str)


def run_agent(
    client: OpenAI,
    model: str,
    tools: GridTools,
    on_step: Callable[[str, dict, dict], None] = lambda name, args, result: None,
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "The feeder breaker just tripped. Restore service."},
    ]
    for _ in range(MAX_STEPS):
        reply = client.chat.completions.create(
            model=model, messages=messages, tools=SCHEMAS, temperature=0
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
