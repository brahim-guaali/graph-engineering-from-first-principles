"""The refund graph from stage 4, made safe to replay.

Two changes, both in the nodes (the engine is stage 4's, unchanged):
  * the refund carries an idempotency key built from the run and the node, so running the
    node again after a crash can't pay twice
  * spending is a field in the state with a budget next to it, and an edge stops the run
    (hands it to a person) when the model has spent more than the budget
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04-checkpoints"))   # stage 4's engine
from agentkit import judgments
from agentkit.trace import NullTracer
from engine import END, StateGraph, add, append, interrupt

APPROVAL_LIMIT = 500

# The state, now with a reducer per field. None means "one writer per round".
CHANNELS = {
    "request": None, "claim": None, "order": None, "policy": None, "fraud": None,
    "run_id": None,       # the thread id, so a side effect can name itself
    "budget": None,       # dollars the model may spend on this run
    "decision": None, "approval_request": None, "approval": None, "payment": None, "reply": None,
    "evidence": append,   # the three lookups each add a line, in the same round
    "spent": add,         # every model call adds what it cost
}


def build(llm, platform, channels=CHANNELS, checkpointer=None, tracer=NullTracer(), use_key=True):
    g = StateGraph(channels)

    def charged(call):
        before = llm.usage.cost
        out = call()
        return out, llm.usage.cost - before

    def classify(s):
        claim, cost = charged(lambda: judgments.classify(llm, s["request"]["message"]))
        return {"claim": claim, "spent": cost}

    def order(s):
        o = platform.get_order(s["claim"].order_id)
        return {"order": o, "evidence": f"order {o['order_id']}: ${o['total']}, {o['minutes_late']} min late"}

    def policy(s):
        p = platform.get_policy()
        return {"policy": p, "evidence": f"policy {p['version']}: {len(p['clauses'])} clauses"}

    def fraud(s):
        f = platform.get_fraud(s["request"]["customer"])
        return {"fraud": f, "evidence": f"fraud score {f['score']}"}

    def decide(s):
        d, cost = charged(lambda: judgments.decide(llm, s["request"]["message"], s["claim"],
                                                   s["order"], s["policy"], s["fraud"]))
        return {"decision": d, "spent": cost}

    def ask_manager(s):
        a = platform.request_approval("refund-graph", s["order"]["order_id"],
                                      s["decision"].amount, s["decision"].reason)
        return {"approval_request": a}

    def approval(s):
        # No polling, no blocking: stop here. The checkpoint holds everything; the process can exit.
        decision = interrupt({"approval_id": s["approval_request"]["id"],
                              "question": f"Refund ${s['decision'].amount:.0f}? ({s['decision'].reason})"})
        return {"approval": decision}

    def refund(s):
        key = f"{s['run_id']}:refund" if use_key else None     # stable across retries of this node
        payment = platform.pay(s["order"]["order_id"], s["decision"].amount, idempotency_key=key)
        if os.environ.get("CRASH_AFTER_PAYMENT"):              # the money moved; now the process dies
            print(f"  💥 crashing right after payment {payment['id']}, before the checkpoint", flush=True)
            os._exit(137)
        return {"payment": payment}

    def over_budget(s):
        text = "Your request needs a closer look; a colleague will get back to you today."
        return {"reply": {"queue": "support-escalations", "text": text,
                          "why": f"model spend ${s['spent']:.5f} > budget ${s['budget']:.5f}"}}

    def reply(s):
        text = s["decision"].customer_message
        if s["approval"] == "rejected":
            text = "We reviewed your request and can't refund this order. A colleague will follow up."
        return {"reply": platform.notify(s["request"]["customer"], text) | {"text": text}}

    def handoff(s):
        return {"reply": {"queue": "customer-support", "text": s["request"]["message"]}}

    kinds = {"classify": "model", "decide": "model", "order": "http", "policy": "http", "fraud": "http",
             "ask_manager": "http", "approval": "person", "refund": "http · money", "reply": "http",
             "handoff": "code", "over_budget": "code"}
    for fn in (classify, order, policy, fraud, decide, ask_manager, approval, refund, reply, handoff,
               over_budget):
        g.add_node(fn.__name__, fn, kinds[fn.__name__])

    g.set_entry("classify")
    g.add_conditional_edges("classify", lambda s: ["order", "policy", "fraud"]       # fan-out
                            if s["claim"].kind == "refund_request" else "handoff",
                            {"order": "refund", "policy": "refund", "fraud": "refund", "handoff": "other"})
    g.add_edge(["order", "policy", "fraud"], "decide")                               # fan-in
    g.add_conditional_edges("decide", route_after_decision,
                            {"ask_manager": "> $500", "refund": "≤ $500", "reply": "credit / refuse",
                             "over_budget": "spent > budget"})
    g.add_edge("ask_manager", "approval")
    g.add_conditional_edges("approval", lambda s: "refund" if s["approval"] == "approved" else "reply",
                            {"refund": "approved", "reply": "rejected"})
    g.add_edge("refund", "reply")
    g.add_edge("reply", END)
    g.add_edge("handoff", END)
    g.add_edge("over_budget", END)
    return g.compile(checkpointer)


def route_after_decision(s):
    """Still plain code. The budget check comes first: past the limit, nothing else runs."""
    if s["budget"] is not None and s["spent"] > s["budget"]:
        return "over_budget"
    d = s["decision"]
    if d.action != "refund":
        return "reply"
    return "ask_manager" if d.amount > APPROVAL_LIMIT else "refund"
