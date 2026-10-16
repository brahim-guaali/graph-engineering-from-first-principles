"""The refund graph from stage 5, with a verifier after the one judgment that moves money.

decide -> verify -> (ok) the usual routing
                 -> (problems, attempts left) decide again, told what was wrong
                 -> (problems, no attempts left) a person

The verifier is plain code and only checks what code can know for sure, so it is sound:
it never rejects a right answer. The engine is still stage 4's.
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
    "attempts": add,      # how many times decide has run
    "problems": None,     # what the verifier found wrong with the last decision
    "decision": None, "approval_request": None, "approval": None, "payment": None, "reply": None,
    "evidence": append,   # the three lookups each add a line, in the same round
    "spent": add,         # every model call adds what it cost
}


def build(llm, platform, channels=CHANNELS, checkpointer=None, tracer=NullTracer(), use_key=True,
          sabotage=None):
    """sabotage: an optional stand-in for judgments.decide, to show the retry path on purpose."""
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
        feedback = "; ".join(s["problems"]) if s["problems"] else None
        d, cost = charged(lambda: (sabotage or judgments.decide)(
            llm, s["request"]["message"], s["claim"], s["order"], s["policy"], s["fraud"], feedback))
        return {"decision": d, "spent": cost, "attempts": 1}

    def verify(s):
        return {"problems": check(s["decision"], s["order"], s["policy"], s["fraud"])}

    def escalate(s):
        text = "Your request needs a closer look; a colleague will get back to you today."
        return {"reply": {"queue": "support-escalations", "text": text,
                          "why": f"decision failed checks {MAX_ATTEMPTS} times: {s['problems']}"}}

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
             "handoff": "code", "over_budget": "code", "verify": "code", "escalate": "code"}
    for fn in (classify, order, policy, fraud, decide, verify, ask_manager, approval, refund, reply,
               handoff, over_budget, escalate):
        g.add_node(fn.__name__, fn, kinds[fn.__name__])

    g.set_entry("classify")
    g.add_conditional_edges("classify", lambda s: ["order", "policy", "fraud"]       # fan-out
                            if s["claim"].kind == "refund_request" else "handoff",
                            {"order": "refund", "policy": "refund", "fraud": "refund", "handoff": "other"})
    g.add_edge(["order", "policy", "fraud"], "decide")                               # fan-in
    g.add_edge("decide", "verify")
    g.add_conditional_edges("verify", route_after_verify,
                            {"ask_manager": "> $500", "refund": "≤ $500", "reply": "credit / refuse",
                             "over_budget": "spent > budget", "decide": "retry with feedback",
                             "escalate": "still wrong"})
    g.add_edge("ask_manager", "approval")
    g.add_conditional_edges("approval", lambda s: "refund" if s["approval"] == "approved" else "reply",
                            {"refund": "approved", "reply": "rejected"})
    g.add_edge("refund", "reply")
    g.add_edge("reply", END)
    g.add_edge("handoff", END)
    g.add_edge("over_budget", END)
    g.add_edge("escalate", END)
    return g.compile(checkpointer)


MAX_ATTEMPTS = 3


def route_after_verify(s):
    """Plain code. Budget first, then the verifier's verdict, then the usual $500 rule."""
    if s["budget"] is not None and s["spent"] > s["budget"]:
        return "over_budget"
    if s["problems"]:
        return "decide" if s["attempts"] < MAX_ATTEMPTS else "escalate"
    d = s["decision"]
    if d.action != "refund":
        return "reply"
    return "ask_manager" if d.amount > APPROVAL_LIMIT else "refund"


def check(d, order, policy, fraud):
    """Every rule here is certain from the facts, so a failure always means the answer is wrong."""
    problems = []
    if d.clause not in policy["clauses"]:
        problems.append(f"clause {d.clause} does not exist; valid clauses are {sorted(policy['clauses'])}")
    if not 0 <= d.amount <= order["total"]:
        problems.append(f"amount ${d.amount} is outside $0 to the order total ${order['total']}")
    if d.action == "refund" and d.amount <= 0:
        problems.append("a refund must have an amount above $0")
    if d.action != "refund" and d.amount != 0:
        problems.append(f"action {d.action} must have amount $0")
    if fraud["score"] > 0.7 and d.action != "refuse":
        problems.append(f"fraud score {fraud['score']} is above 0.7, so §4 requires refuse")
    if d.clause == "§1" and (order["minutes_late"] <= 45 or d.amount != order["total"]):
        problems.append(f"§1 needs more than 45 min late ({order['minutes_late']}) and the full total "
                        f"${order['total']}")
    if d.clause == "§6" and order["minutes_late"] > 45:
        problems.append(f"§6 is for 45 minutes late or less; this order was {order['minutes_late']}")
    if (d.clause == "§5") != (order["status"] == "cancelled_after_cooking"):
        problems.append(f"§5 applies exactly when the order was cancelled after cooking "
                        f"(status: {order['status']})")
    return problems
