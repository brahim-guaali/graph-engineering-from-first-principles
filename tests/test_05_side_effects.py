import sys

import pytest

from agentkit.cases import CASES
from agentkit.platform import Platform
from fakes import ScriptedStructured
from judges import answers
from stages import load

refund = load("05-side-effects", "refund")
Checkpointer = sys.modules["checkpoint"].Checkpointer
DESSERT = next(c for c in CASES if c["id"] == "missing-dessert")


@pytest.fixture
def cp(tmp_path):
    return Checkpointer(tmp_path / "cp.db")


def replay_refund(platform, cp, use_key):
    """Run to the end, then replay the refund round from the checkpoint before it."""
    g = refund.build(ScriptedStructured(answers(order_id="A-8120", amount=18)), Platform(platform),
                     checkpointer=cp, use_key=use_key)
    g.invoke({"request": DESSERT, "run_id": "r1"}, thread="r1", log=lambda *_: None)
    before_refund = next(c for c in cp.history("r1") if c["next"] == ["refund"])
    g.fork("r1", before_refund["step"], {})
    replay = cp.threads()[0]
    g.resume(replay, log=lambda *_: None)
    return Platform(platform).payments("A-8120")


def test_without_a_key_a_replay_pays_twice(platform, cp):
    assert replay_refund(platform, cp, use_key=False)["count"] == 2


def test_with_a_key_a_replay_pays_once(platform, cp):
    assert replay_refund(platform, cp, use_key=True)["count"] == 1


def test_over_budget_stops_before_any_payment(platform, cp):
    llm = ScriptedStructured(answers(order_id="A-8120", amount=18))
    g = refund.build(llm, Platform(platform), checkpointer=cp)
    expensive = {"claim": refund.judgments.Classification(kind="refund_request", order_id="A-8120"),
                 "spent": 1.0}                                 # pretend classify cost $1
    g.g.nodes["classify"] = lambda s: expensive
    result = g.invoke({"request": DESSERT, "run_id": "r2", "budget": 0.5}, thread="r2", log=lambda *_: None)
    assert result["state"]["reply"]["queue"] == "support-escalations"
    assert Platform(platform).payments("A-8120")["count"] == 0
