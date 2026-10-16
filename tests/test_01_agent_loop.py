import httpx
import pytest

from agentkit.cases import REFUND
from agentkit.platform import Platform
from fakes import ScriptedChat
from stages import load

agent = load("01-agent-loop", "agent")

LOOKUPS = [("get_order", {"order_id": "A-7731"}), ("get_refund_policy", {}),
           ("get_fraud_signals", {"customer": "C-1042"})]


def test_loop_runs_the_tools_the_model_picks(platform):
    llm = ScriptedChat([LOOKUPS, [("issue_refund", {"order_id": "A-7731", "amount": 640})], "done"])
    answer, _, trace = agent.agent_loop(REFUND, llm, Platform(platform), log=lambda *_: None)
    assert answer == "done"
    assert [t[0] for t in trace] == ["get_order", "get_refund_policy", "get_fraud_signals", "issue_refund"]


def test_one_timeout_loses_the_whole_run(platform):
    Platform(platform).inject_faults(fraud=1)
    llm = ScriptedChat([LOOKUPS, "never reached"])
    with pytest.raises(httpx.TimeoutException):
        agent.agent_loop(REFUND, llm, Platform(platform, timeout=1), log=lambda *_: None)


def test_waiting_for_a_person_blocks_then_gives_up(platform):
    llm = ScriptedChat([[("request_manager_approval", {"order_id": "A-7731", "amount": 640, "reason": "late"})]])
    with pytest.raises(agent.ManagerAway):
        agent.agent_loop(REFUND, llm, Platform(platform), approval_wait=1, log=lambda *_: None)
