from langgraph.types import Command

from agentkit.cases import CASES, REFUND
from agentkit.platform import Platform
from fakes import ScriptedStructured
from judges import answers
from stages import load

graph = load("07-langgraph", "graph")


def test_the_langgraph_version_pauses_resumes_and_pays_once(platform, tmp_path):
    g = graph.build(ScriptedStructured(answers()), Platform(platform), db=tmp_path / "lg.db")
    config = {"configurable": {"thread_id": "lg1"}}
    g.invoke({"request": REFUND}, config)
    pending = g.get_state(config)
    assert pending.next == ("approval",)
    state = g.invoke(Command(resume="approved"), config)
    assert len(state["evidence"]) == 3                   # three parallel writers, one reducer
    assert Platform(platform).payments("A-7731")["count"] == 1
    assert Platform(platform).payments("A-7731")["payments"][0]["idempotency_key"] == "lg1:refund"


def test_a_retry_policy_absorbs_a_hung_lookup(platform, tmp_path):
    Platform(platform).inject_faults(fraud=1)
    dessert = next(c for c in CASES if c["id"] == "missing-dessert")
    g = graph.build(ScriptedStructured(answers(order_id="A-8120", amount=18, clause="§2")),
                    Platform(platform, timeout=1), db=tmp_path / "lg.db")
    state = g.invoke({"request": dessert}, {"configurable": {"thread_id": "lg2"}})
    assert state["payment"]["amount"] == 18
