"""Stage 6 demo: the verifier catches a wrong decision and decide retries with feedback.

    make platform                                  # in another terminal
    python 06-reliability/run.py                   # the verifier demo
    python 06-reliability/arithmetic.py            # why fewer, checked decisions win
    python 06-reliability/evaluate.py              # loop vs graph on 12 cases (~25 min)

The real model rarely gets this case wrong, so the demo makes its *first* answer wrong on
purpose (a clause that doesn't exist, double the amount) to show what the graph does next.
"""
import sys
import uuid

from agentkit import judgments
from agentkit.cases import CASES
from agentkit.llm import Gemini
from agentkit.platform import Platform
from agentkit.trace import Tracer
from refund import build
from checkpoint import Checkpointer

sys.stdout.reconfigure(line_buffering=True)


def wrong_once():
    calls = {"n": 0}

    def decide(llm, *args):
        calls["n"] += 1
        d = judgments.decide(llm, *args)
        if calls["n"] == 1:
            print(f"  (the first answer is spoiled on purpose: {d.clause} -> §7, ${d.amount:g} -> ${d.amount * 2:g})")
            d = d.model_copy(update={"clause": "§7", "amount": d.amount * 2})
        return d
    return decide


if __name__ == "__main__":
    platform = Platform()
    platform.check()
    platform.reset()
    case = next(c for c in CASES if c["id"] == "late-grill")
    print("\n=== The verifier catches a wrong decision; decide retries with the problems as feedback ===")
    llm, cp, thread = Gemini(), Checkpointer(), "t-" + uuid.uuid4().hex[:6]
    tracer = Tracer("6 · reliability", "verifier: spoiled first answer", usage=llm.usage, run_id=thread)
    g = build(llm, platform, checkpointer=cp, tracer=tracer, sabotage=wrong_once())
    tracer.start(g.g.spec())
    result = g.invoke({"request": case, "run_id": thread}, thread=thread, tracer=tracer)
    s = result["state"]
    shown = None
    for c in cp.history(thread):
        if c["state"].get("problems") and c["state"]["problems"] != shown:
            shown = c["state"]["problems"]
            print(f"  checkpoint {c['step']}: verify found {shown}")
    d = s["decision"]
    tracer.finished("done", f"{d.action} ${d.amount:g} under {d.clause} after {s['attempts']} attempts")
    print(f"  -> {d.action} ${d.amount:g} under {d.clause} after {s['attempts']} attempts; "
          f"paid ${platform.payments('A-9310')['total']:g} once")
    print("""
What this stage adds:
  - a sound check after the one judgment that moves money, and a retry with the reasons
  - the evaluation: both designs on 12 cases, scored on what actually happened (money, approvals)
Where it still breaks:
  - it's our own engine. Next: 07-langgraph builds the same graph on a real framework,
    with a loop inside one node, and asks when you shouldn't use a graph at all.""")
