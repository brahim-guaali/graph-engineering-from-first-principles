import inspect
import time

import httpx
import pytest

from agentkit.cases import REFUND
from agentkit.platform import Platform
from fakes import ScriptedStructured
from judges import answers
from stages import load

refund = load("03-supersteps", "refund")
engine = inspect.getmodule(refund.StateGraph)    # the same module refund.py uses


def test_lookups_run_in_parallel(platform):
    t0 = time.monotonic()
    state = refund.build(ScriptedStructured(answers(amount=18)), Platform(platform)).invoke(
        {"request": REFUND}, log=lambda *_: None)
    assert time.monotonic() - t0 < 3 * 0.2 + 0.3          # three 0.2 s lookups, overlapped
    assert len(state["evidence"]) == 3


def test_parallel_writes_without_a_reducer_fail_loudly(platform):
    build = refund.build(ScriptedStructured(answers()), Platform(platform),
                         dict(refund.CHANNELS, evidence=None))
    with pytest.raises(engine.InvalidUpdateError, match="no reducer"):
        build.invoke({"request": REFUND}, log=lambda *_: None)


def test_nodes_in_one_round_read_the_same_snapshot():
    g = engine.StateGraph({"x": None, "seen_by_b": None, "seen_by_c": None})
    g.add_node("a", lambda s: {"x": 1})
    g.add_node("b", lambda s: {"x": 2, "seen_by_b": s["x"]})
    g.add_node("c", lambda s: {"seen_by_c": s["x"]})
    g.set_entry("a")
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    state = g.compile().invoke({}, log=lambda *_: None)
    assert state["seen_by_b"] == 1 and state["seen_by_c"] == 1   # c never sees b's write


def test_one_failed_node_still_loses_the_run(platform):
    Platform(platform).inject_faults(fraud=1)
    with pytest.raises(httpx.TimeoutException):
        refund.build(ScriptedStructured(answers()), Platform(platform, timeout=1)).invoke(
            {"request": REFUND}, log=lambda *_: None)
