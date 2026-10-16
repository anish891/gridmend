import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from gridmend.flisr.execute import restore
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.policy import (
    ModelError, NotHealthyError, PolicyError, clear_conditions, current_context, set_conditions,
    validate,
)
from gridmend.sim import apply_fault

NAMES = ["hospital", "cooling centre", "fire station", "water pumping", "telecom"]

HEAT = {"priorities": [
    {"facility": "hospital", "weight": 10, "rule": "2.1"},
    {"facility": "cooling centre", "weight": 10, "rule": "3.1 heat advisory, centre open"},
    {"facility": "fire station", "weight": 6, "rule": "3.2 backup generation"},
    {"facility": "water pumping", "weight": 10, "rule": "2.3"},
    {"facility": "telecom", "weight": 10, "rule": "2.4"},
]}


class FixedModel:
    """Stands in for the LLM with one canned reply, keeping the prompt it was sent."""

    def __init__(self, reply: str):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.reply = reply
        self.prompt = ""

    def create(self, **kwargs):
        self.prompt = kwargs["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def weights(graph) -> dict[str, tuple[int, bool]]:
    rows = graph.ro_query(
        "MATCH (b:Bus) WHERE b.facility <> '' RETURN b.facility, b.weight, b.critical"
    ).result_set
    return {f: (w, c) for f, w, c in rows}


@pytest.mark.parametrize("entry, problem", [
    ({"facility": "stadium", "weight": 5, "rule": "3.1"}, "unknown facility"),
    ({"facility": "hospital", "weight": 25, "rule": "2.1"}, "outside"),
    ({"facility": "hospital", "weight": 2.5, "rule": "2.1"}, "whole number"),
    ({"facility": "hospital", "weight": True, "rule": "2.1"}, "whole number"),
    ({"facility": "hospital", "weight": 10, "rule": " "}, "no policy rule"),
])
def test_invalid_priorities_are_refused(entry, problem):
    with pytest.raises(PolicyError, match=problem):
        validate({"priorities": [entry]}, NAMES)


def test_facilities_left_out_keep_their_standing_priority():
    chosen = validate({"priorities": [{"facility": "telecom", "weight": 12, "rule": "3.3"}]}, NAMES)
    by_name = {p.facility: p.weight for p in chosen}
    assert by_name == {"hospital": 10, "cooling centre": 1, "fire station": 10,
                       "water pumping": 10, "telecom": 12}


def test_heat_advisory_raises_the_cooling_centre_over_the_fire_station(falkordb, graph):
    model = FixedModel("<think>policy 3.1 and 3.2 apply</think>" + json.dumps(HEAT))
    context = set_conditions(graph, model, "test-model", "Heat advisory, 44 C, cooling centre open.")

    assert "Heat advisory, 44 C" in model.prompt and "3.2 During a heat advisory" in model.prompt
    assert context["conditions"].startswith("Heat advisory")
    assert weights(graph)["cooling centre"] == (10, True)
    assert weights(graph)["fire station"] == (6, True)

    apply_fault(graph, "4-5")
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    _, executed = restore(falkordb, graph, incident_id, approve=lambda plan: True)

    # On a normal day this fault restores the hospital and fire station (test_execute).
    assert executed["critical"] == ["hospital", "cooling centre"]
    explanation = explain_incident(graph, incident_id)
    assert explanation["context"]["conditions"].startswith("Heat advisory")
    fire = next(c for c in explanation["critical"] if c["facility"] == "fire station")
    assert "higher total priority" in fire["reason"]


def test_a_bad_reply_changes_nothing(graph):
    before = weights(graph)
    with pytest.raises(PolicyError):
        set_conditions(graph, FixedModel("I would raise the cooling centre."), "m", "heatwave")
    with pytest.raises(PolicyError):
        set_conditions(graph, FixedModel('{"priorities": [{"facility": "mall", "weight": 9, '
                                         '"rule": "3.1"}]}'), "m", "heatwave")
    assert weights(graph) == before
    assert current_context(graph)["conditions"] is None


def test_an_unreachable_model_is_reported_not_raised(graph):
    class Down(FixedModel):
        def create(self, **kwargs):
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://llm.test"))

    before = weights(graph)
    with pytest.raises(ModelError, match="could not answer"):
        set_conditions(graph, Down(""), "m", "heatwave")
    assert weights(graph) == before


def test_conditions_are_fixed_during_an_incident(graph):
    apply_fault(graph, "4-5")
    with pytest.raises(NotHealthyError):
        set_conditions(graph, FixedModel(json.dumps(HEAT)), "m", "heatwave")
    with pytest.raises(NotHealthyError):
        clear_conditions(graph)


def test_clearing_returns_to_standing_priorities(graph):
    set_conditions(graph, FixedModel(json.dumps(HEAT)), "m", "heatwave")
    context = clear_conditions(graph)

    assert context["conditions"] is None
    assert weights(graph)["cooling centre"] == (1, False)
    assert weights(graph)["fire station"] == (10, True)
    zone_10 = graph.ro_query("MATCH (z:Zone {id: 10}) RETURN z.p_kw, z.weighted_kw").result_set[0]
    assert zone_10[0] == pytest.approx(zone_10[1])
