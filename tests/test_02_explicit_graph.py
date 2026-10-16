import httpx
import pytest

from agentkit.cases import REFUND
from agentkit.ops import ManagerAtDesk
from agentkit.platform import Platform
from fakes import ScriptedStructured
from judges import answers
from stages import load

refund = load("02-explicit-graph", "refund")


def run(platform_url, **kw):
    llm = ScriptedStructured(answers(**kw))
    return refund.build(llm, Platform(platform_url), approval_wait=10).run(
        {"request": REFUND}, "classify", log=lambda *_: None)


def test_big_refund_goes_through_the_manager(platform):
    with ManagerAtDesk(delay=0.2):
        state, path = run(platform)
    assert path == ["classify", "order", "policy", "fraud", "decide", "approval", "refund", "reply"]
    assert Platform(platform).payments("A-7731")["total"] == 640


def test_small_refund_skips_the_manager(platform):
    _, path = run(platform, amount=18)
    assert "approval" not in path and "refund" in path


def test_other_messages_are_handed_off(platform):
    _, path = run(platform, kind="other")
    assert path == ["classify", "handoff"]


def test_a_node_cannot_write_a_field_it_does_not_own(platform):
    g = refund.build(ScriptedStructured(answers()), Platform(platform))
    _, reads, writes = g.nodes["fraud"]
    g.nodes["fraud"] = (lambda s: {"fraud": {}, "decision": "x"}, reads, writes)
    with pytest.raises(PermissionError):
        g.run({"request": REFUND}, "classify", log=lambda *_: None)


def test_a_failed_node_still_loses_the_run(platform):
    Platform(platform).inject_faults(fraud=1)
    llm = ScriptedStructured(answers())
    with pytest.raises(httpx.TimeoutException):
        refund.build(llm, Platform(platform, timeout=1)).run({"request": REFUND}, "classify",
                                                            log=lambda *_: None)
