import json
import re

from openai import OpenAI

from gridmend.config import Settings, load_settings


def make_client(settings: Settings | None = None) -> tuple[OpenAI, str]:
    settings = settings or load_settings()
    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    return client, settings.llm_model


def parse_json_object(text: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating prose, code fences or a
    <think> block around it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    return json.loads(match.group(0))


LLM_ONLY_PROMPT = """You operate a radial power distribution feeder. A fault has been located
and isolated. Choose switch operations that restore as much healthy, dark load as possible.

Rules: keep the faulted zone de-energized, keep the feeder radial (no loops), keep every line
within its kVA rating, keep every energized bus at or above {v_min} pu. Prefer critical loads,
then more load, then fewer switch operations.

Reply with only a JSON object: {{"close": ["switch id", ...], "open": ["switch id", ...]}}

{description}
"""


def llm_only_switching(client: OpenAI, model: str, description: str, v_min: float) -> dict:
    reply = client.chat.completions.create(
        model=model,
        temperature=0,
        # Reasoning models spend most of their budget thinking before the JSON appears.
        max_tokens=16000,
        messages=[{"role": "user",
                   "content": LLM_ONLY_PROMPT.format(description=description, v_min=v_min)}],
    )
    plan = parse_json_object(reply.choices[0].message.content)
    return {"close": list(plan.get("close", [])), "open": list(plan.get("open", []))}
