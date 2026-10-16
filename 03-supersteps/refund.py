"""The refund graph, now run in supersteps: the three lookups fan out and fan back in."""
import time
import uuid

from agentkit import judgments
from engine import END, StateGraph, add, append

APPROVAL_LIMIT = 500

# The state, now with a reducer per field. None means "one writer per round".
CHANNELS = {
    "request": None, "claim": None, "order": None, "policy": None, "fraud": None,
    "decision": None, "approval": None, "payment": None, "reply": None,
    "evidence": append,   # the three lookups each add a line, in the same round
    "spent": add,         # every model call adds what it cost
}


class ManagerAway(Exception):
    pass


def build(llm, platform, channels=CHANNELS, approval_wait=20):
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

    def approval(s):
        a = platform.request_approval("ss-" + uuid.uuid4().hex[:6], s["order"]["order_id"],
                                      s["decision"].amount, s["decision"].reason)
        deadline = time.monotonic() + approval_wait
        while time.monotonic() < deadline:
            status = platform.get_approval(a["id"])["status"]
            if status != "pending":
                return {"approval": status}
            time.sleep(1)
        raise ManagerAway(f"no decision on {a['id']} after {approval_wait}s")

    def refund(s):
        return {"payment": platform.pay(s["order"]["order_id"], s["decision"].amount)}

    def reply(s):
        text = s["decision"].customer_message
        if s["approval"] == "rejected":
            text = "We reviewed your request and can't refund this order. A colleague will follow up."
        return {"reply": platform.notify(s["request"]["customer"], text) | {"text": text}}

    def handoff(s):
        return {"reply": {"queue": "customer-support", "text": s["request"]["message"]}}

    for fn in (classify, order, policy, fraud, decide, approval, refund, reply, handoff):
        g.add_node(fn.__name__, fn)

    g.set_entry("classify")
    g.add_conditional_edges("classify", lambda s: ["order", "policy", "fraud"]       # fan-out
                            if s["claim"].kind == "refund_request" else "handoff")
    g.add_edge(["order", "policy", "fraud"], "decide")                               # fan-in
    g.add_conditional_edges("decide", route_after_decision)
    g.add_conditional_edges("approval", lambda s: "refund" if s["approval"] == "approved" else "reply")
    g.add_edge("refund", "reply")
    g.add_edge("reply", END)
    g.add_edge("handoff", END)
    return g.compile()


def route_after_decision(s):
    d = s["decision"]
    if d.action != "refund":
        return "reply"
    return "approval" if d.amount > APPROVAL_LIMIT else "refund"
