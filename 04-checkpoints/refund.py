"""The refund graph with checkpoints. The approval is now two nodes: ask_manager sends the
request (once), and approval pauses the run with interrupt() until the manager answers."""
from agentkit import judgments
from agentkit.trace import NullTracer
from engine import END, StateGraph, add, append, interrupt

APPROVAL_LIMIT = 500

# The state, now with a reducer per field. None means "one writer per round".
CHANNELS = {
    "request": None, "claim": None, "order": None, "policy": None, "fraud": None,
    "decision": None, "approval_request": None, "approval": None, "payment": None, "reply": None,
    "evidence": append,   # the three lookups each add a line, in the same round
    "spent": add,         # every model call adds what it cost
}


def build(llm, platform, channels=CHANNELS, checkpointer=None, tracer=NullTracer()):
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
        return {"payment": platform.pay(s["order"]["order_id"], s["decision"].amount)}

    def reply(s):
        text = s["decision"].customer_message
        if s["approval"] == "rejected":
            text = "We reviewed your request and can't refund this order. A colleague will follow up."
        return {"reply": platform.notify(s["request"]["customer"], text) | {"text": text}}

    def handoff(s):
        return {"reply": {"queue": "customer-support", "text": s["request"]["message"]}}

    kinds = {"classify": "model", "decide": "model", "order": "http", "policy": "http", "fraud": "http",
             "ask_manager": "http", "approval": "person", "refund": "http · money", "reply": "http",
             "handoff": "code"}
    for fn in (classify, order, policy, fraud, decide, ask_manager, approval, refund, reply, handoff):
        g.add_node(fn.__name__, fn, kinds[fn.__name__])

    g.set_entry("classify")
    g.add_conditional_edges("classify", lambda s: ["order", "policy", "fraud"]       # fan-out
                            if s["claim"].kind == "refund_request" else "handoff",
                            {"order": "refund", "policy": "refund", "fraud": "refund", "handoff": "other"})
    g.add_edge(["order", "policy", "fraud"], "decide")                               # fan-in
    g.add_conditional_edges("decide", route_after_decision,
                            {"ask_manager": "> $500", "refund": "≤ $500", "reply": "credit / refuse"})
    g.add_edge("ask_manager", "approval")
    g.add_conditional_edges("approval", lambda s: "refund" if s["approval"] == "approved" else "reply",
                            {"refund": "approved", "reply": "rejected"})
    g.add_edge("refund", "reply")
    g.add_edge("reply", END)
    g.add_edge("handoff", END)
    return g.compile(checkpointer)


def route_after_decision(s):
    d = s["decision"]
    if d.action != "refund":
        return "reply"
    return "ask_manager" if d.amount > APPROVAL_LIMIT else "refund"
