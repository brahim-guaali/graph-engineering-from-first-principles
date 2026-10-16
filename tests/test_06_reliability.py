import sys

import pytest

from agentkit.judgments import Decision
from agentkit.platform import Platform
from agentkit.cases import CASES
from fakes import ScriptedStructured
from judges import answers
from stages import load

refund = load("06-reliability", "refund")
Checkpointer = sys.modules["checkpoint"].Checkpointer
ORDER = {"total": 72, "minutes_late": 50, "status": "delivered"}
POLICY = {"clauses": {f"§{i}": "" for i in range(1, 7)}}
CLEAN = {"score": 0.08}


def d(action="refund", amount=72, clause="§1"):
    return Decision(action=action, amount=amount, clause=clause, reason="", customer_message="")


def test_a_right_answer_passes():
    assert refund.check(d(), ORDER, POLICY, CLEAN) == []


@pytest.mark.parametrize("decision, fraud, words", [
    (d(clause="§7"), CLEAN, "does not exist"),
    (d(amount=144), CLEAN, "outside"),
    (d(action="credit", amount=5, clause="§6"), CLEAN, "must have amount $0"),
    (d(), {"score": 0.9}, "requires refuse"),
    (d(amount=40), CLEAN, "full total"),
    (d(action="credit", amount=0, clause="§6"), CLEAN, "45 minutes late or less"),
])
def test_wrong_answers_are_caught(decision, fraud, words):
    assert any(words in p for p in refund.check(decision, ORDER, POLICY, fraud))


def test_a_wrong_decision_is_retried_with_feedback(platform, tmp_path):
    case = next(c for c in CASES if c["id"] == "late-grill")
    seen = []

    def answer(schema, prompt):
        if schema == "Classification":
            return {"kind": "refund_request", "order_id": "A-9310", "items_claimed": []}
        seen.append("rejected by a check" in prompt)
        bad = len(seen) == 1
        return {"action": "refund", "amount": 144 if bad else 72, "clause": "§7" if bad else "§1",
                "reason": "late", "customer_message": "ok"}

    g = refund.build(ScriptedStructured(answer), Platform(platform), checkpointer=Checkpointer(tmp_path / "c.db"))
    result = g.invoke({"request": case, "run_id": "v1"}, thread="v1", log=lambda *_: None)
    assert seen == [False, True]                       # the second attempt was told what was wrong
    assert result["state"]["attempts"] == 2
    assert Platform(platform).payments("A-9310")["total"] == 72


def test_still_wrong_after_three_attempts_goes_to_a_person(platform, tmp_path):
    case = next(c for c in CASES if c["id"] == "late-grill")
    llm = ScriptedStructured(answers(order_id="A-9310", amount=144, clause="§7"))
    g = refund.build(llm, Platform(platform), checkpointer=Checkpointer(tmp_path / "c.db"))
    result = g.invoke({"request": case, "run_id": "v2"}, thread="v2", log=lambda *_: None)
    assert result["state"]["reply"]["queue"] == "support-escalations"
    assert Platform(platform).payments("A-9310")["count"] == 0
