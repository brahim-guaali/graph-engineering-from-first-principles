"""The refund agent as an explicit graph: state first, then nodes, then edges."""
import time
import uuid

from agentkit import judgments
from agentkit.trace import NullTracer
from graph import END, Graph

APPROVAL_LIMIT = 500

# 1. The state, written down before any prompt.
STATE = {
    "request":  "customer id and message",
    "claim":    "refund request or not, order id, items named (model judgment)",
    "order":    "order record from the platform",
    "policy":   "refund policy clauses",
    "fraud":    "fraud score and signals",
    "decision": "action, amount, clause, reason, message (model judgment)",
    "approval": "approved | rejected, from an operations manager",
    "payment":  "payment record from the platform",
    "reply":    "what the customer was told",
}


class ManagerAway(Exception):
    pass


def build(llm, platform, approval_wait=20, tracer=NullTracer()):
    g = Graph(STATE)

    # 2. Nodes: one job each, reading only what they need, writing only what they own.
    @g.node("classify", reads=["request"], writes=["claim"], kind="model")
    def classify(s):
        return {"claim": judgments.classify(llm, s["request"]["message"])}

    @g.node("order", reads=["claim"], writes=["order"], kind="http")
    def order(s):
        return {"order": platform.get_order(s["claim"].order_id)}

    @g.node("policy", writes=["policy"], kind="http")
    def policy(s):
        return {"policy": platform.get_policy()}

    @g.node("fraud", reads=["request"], writes=["fraud"], kind="http")
    def fraud(s):
        return {"fraud": platform.get_fraud(s["request"]["customer"])}

    @g.node("decide", reads=["request", "claim", "order", "policy", "fraud"], writes=["decision"], kind="model")
    def decide(s):
        return {"decision": judgments.decide(llm, s["request"]["message"], s["claim"],
                                             s["order"], s["policy"], s["fraud"])}

    @g.node("approval", reads=["order", "decision"], writes=["approval"], kind="person")
    def approval(s):
        a = platform.request_approval("graph-" + uuid.uuid4().hex[:6], s["order"]["order_id"],
                                      s["decision"].amount, s["decision"].reason)
        tracer.node_waiting("approval", f"approval {a['id']} sent to the operations manager")
        deadline = time.monotonic() + approval_wait
        while time.monotonic() < deadline:             # still a blocking wait, for now
            status = platform.get_approval(a["id"])["status"]
            if status != "pending":
                return {"approval": status}
            time.sleep(1)
        raise ManagerAway(f"no decision on {a['id']} after {approval_wait}s")

    @g.node("refund", reads=["order", "decision"], writes=["payment"], kind="http · money")
    def refund(s):
        return {"payment": platform.pay(s["order"]["order_id"], s["decision"].amount)}

    @g.node("reply", reads=["request", "decision", "approval"], writes=["reply"], kind="http")
    def reply(s):
        text = s["decision"].customer_message
        if s["approval"] == "rejected":
            text = "We reviewed your request and can't refund this order. A colleague will follow up."
        return {"reply": platform.notify(s["request"]["customer"], text) | {"text": text}}

    @g.node("handoff", reads=["request"], writes=["reply"], kind="code")
    def handoff(s):
        return {"reply": {"queue": "customer-support", "text": s["request"]["message"]}}

    # 3. Edges: fixed where the order is known, conditional (plain code) where it isn't.
    g.branch("classify", lambda s: "order" if s["claim"].kind == "refund_request" else "handoff",
             {"order": "refund", "handoff": "other"})
    g.edge("order", "policy")
    g.edge("policy", "fraud")
    g.edge("fraud", "decide")
    g.branch("decide", route_after_decision,
             {"approval": "> $500", "refund": "≤ $500", "reply": "credit / refuse"})
    g.branch("approval", lambda s: "refund" if s["approval"] == "approved" else "reply",
             {"refund": "approved", "reply": "rejected"})
    g.edge("refund", "reply")
    g.edge("reply", END)
    g.edge("handoff", END)
    return g


def route_after_decision(s):
    """Plain code, not a model: the $500 rule can't be wrong one time in a hundred."""
    d = s["decision"]
    if d.action != "refund":
        return "reply"
    return "approval" if d.amount > APPROVAL_LIMIT else "refund"
