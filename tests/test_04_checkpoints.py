import sys

import httpx
import pytest

from agentkit.cases import REFUND
from agentkit.platform import Platform
from fakes import ScriptedStructured
from judges import answers
from stages import load

refund = load("04-checkpoints", "refund")
Checkpointer = sys.modules["checkpoint"].Checkpointer   # the copy refund's engine imported


@pytest.fixture
def cp(tmp_path):
    return Checkpointer(tmp_path / "cp.db")


def test_a_failed_round_keeps_its_finished_nodes(platform, cp):
    Platform(platform).inject_faults(fraud=1)
    llm = ScriptedStructured(answers(amount=18))
    g = refund.build(llm, Platform(platform, timeout=1), checkpointer=cp)
    with pytest.raises(httpx.TimeoutException):
        g.invoke({"request": REFUND}, thread="t1", log=lambda *_: None)
    assert sorted(cp.saved_writes("t1", 2)) == ["order", "policy"]
    calls_before = llm.usage.calls
    result = refund.build(llm, Platform(platform), checkpointer=cp).resume("t1", log=lambda *_: None)
    assert result["status"] == "done"
    assert llm.usage.calls == calls_before + 1          # only decide; classify was not re-run


def test_a_person_pauses_the_run_and_it_resumes_later(platform, cp):
    llm = ScriptedStructured(answers(amount=640))
    result = refund.build(llm, Platform(platform), checkpointer=cp).invoke(
        {"request": REFUND}, thread="t2", log=lambda *_: None)
    assert result["status"] == "interrupted"
    assert Platform(platform).payments("A-7731")["count"] == 0
    # a different process, days later: a fresh graph object, same checkpointer file
    result = refund.build(llm, Platform(platform), checkpointer=cp).resume(
        "t2", "approved", log=lambda *_: None)
    assert result["status"] == "done"
    assert Platform(platform).payments("A-7731")["total"] == 640


def test_time_travel_forks_an_old_checkpoint(platform, cp):
    llm = ScriptedStructured(answers(amount=18))
    g = refund.build(llm, Platform(platform), checkpointer=cp)
    g.invoke({"request": REFUND}, thread="t3", log=lambda *_: None)
    new = g.fork("t3", 2, {"fraud": {"score": 0.99}})
    assert cp.latest(new)["state"]["fraud"]["score"] == 0.99
    assert cp.latest(new)["state"]["order"] == cp.at("t3", 2)["state"]["order"]
