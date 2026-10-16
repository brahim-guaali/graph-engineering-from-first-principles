"""Stage 7: the same refund graph on LangGraph, a real framework.

Everything we built by hand has a name here:
  our engine (stages 3-4)       -> StateGraph, which runs in Pregel supersteps
  reducers                      -> Annotated[type, reducer] on the state
  fan-out / fan-in              -> add_edge(a, [b, c, d]) / add_edge([b, c, d], e)
  checkpoints, resume, history  -> SqliteSaver, invoke(None, config), get_state_history
  interrupt() for a person      -> langgraph.types.interrupt and Command(resume=...)
  fork()                        -> update_state(old_config, values)
  "retry only the failed node"  -> RetryPolicy on the node itself

And the hybrid: the `support` node runs a small agent loop inside it, for open-ended work the
graph can't plan in advance.
"""
import operator
import sqlite3
from pathlib import Path
from typing import Annotated, Optional, TypedDict

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt

from agentkit import judgments
from agentkit.trace import NullTracer
from support import support_agent
from verify import check

APPROVAL_LIMIT = 500
MAX_ATTEMPTS = 3
DB = Path(__file__).resolve().parents[1] / "var" / "langgraph.db"


class RefundState(TypedDict, total=False):
    request: dict
    budget: Optional[float]
    claim: dict
    order: dict
    policy: dict
    fraud: dict
    evidence: Annotated[list, operator.add]     # three parallel writers: append
    spent: Annotated[float, operator.add]       # every model call adds its cost
    attempts: Annotated[int, operator.add]
    decision: dict
    problems: list
    approval_request: dict
    approval: str
    payment: dict
    reply: dict


def build(llm, platform, tracer=NullTracer(), db=DB):
    def traced(name, fn):
        """Report each node to the live viewer; LangGraph does the rest."""
        def node(state, config):
            tracer.node_started(name, round=config.get("metadata", {}).get("langgraph_step"))
            try:
                update = fn(state, config)
            except Exception as e:
                if type(e).__name__ == "GraphInterrupt":
                    tracer.node_waiting(name, "waiting for the operations manager")
                else:
                    tracer.node_failed(name, e)
                raise
            tracer.node_finished(name, update)
            return update
        return node

    def charged(call):
        before = llm.usage.cost
        out = call()
        return out, llm.usage.cost - before

    def classify(s, config):
        claim, cost = charged(lambda: judgments.classify(llm, s["request"]["message"]))
        return {"claim": claim.model_dump(), "spent": cost}

    def order(s, config):
        o = platform.get_order(s["claim"]["order_id"])
        return {"order": o, "evidence": [f"order {o['order_id']}: ${o['total']}, {o['minutes_late']} min late"]}

    def policy(s, config):
        p = platform.get_policy()
        return {"policy": p, "evidence": [f"policy {p['version']}"]}

    def fraud(s, config):
        f = platform.get_fraud(s["request"]["customer"])
        return {"fraud": f, "evidence": [f"fraud score {f['score']}"]}

    def decide(s, config):
        feedback = "; ".join(s.get("problems") or []) or None
        claim = judgments.Classification(**s["claim"])
        d, cost = charged(lambda: judgments.decide(llm, s["request"]["message"], claim, s["order"],
                                                   s["policy"], s["fraud"], feedback))
        return {"decision": d.model_dump(), "spent": cost, "attempts": 1}

    def verify(s, config):
        return {"problems": check(judgments.Decision(**s["decision"]), s["order"], s["policy"], s["fraud"])}

    def ask_manager(s, config):
        d = s["decision"]
        return {"approval_request": platform.request_approval(
            config["configurable"]["thread_id"], s["order"]["order_id"], d["amount"], d["reason"])}

    def approval(s, config):
        answer = interrupt({"approval_id": s["approval_request"]["id"],
                            "question": f"Refund ${s['decision']['amount']:g}? ({s['decision']['reason']})"})
        return {"approval": answer}

    def refund(s, config):
        key = f"{config['configurable']['thread_id']}:refund"          # idempotency: run + node
        return {"payment": platform.pay(s["order"]["order_id"], s["decision"]["amount"], idempotency_key=key)}

    def reply(s, config):
        text = s["decision"]["customer_message"]
        if s.get("approval") == "rejected":
            text = "We reviewed your request and can't refund this order. A colleague will follow up."
        return {"reply": platform.notify(s["request"]["customer"], text) | {"text": text}}

    def support(s, config):
        # The hybrid: open-ended work, so a small agent loop runs *inside* this one node.
        return {"reply": support_agent(llm, platform, s["request"], tracer)}

    def escalate(s, config):
        return {"reply": {"queue": "support-escalations", "why": s.get("problems") or "over budget"}}

    lookups = RetryPolicy(max_attempts=3, initial_interval=0.5,
                          retry_on=(httpx.TimeoutException, httpx.ConnectError))
    g = StateGraph(RefundState)
    for name, fn in [("classify", classify), ("decide", decide), ("verify", verify),
                     ("ask_manager", ask_manager), ("approval", approval), ("refund", refund),
                     ("reply", reply), ("support", support), ("escalate", escalate)]:
        g.add_node(name, traced(name, fn))
    for name, fn in [("order", order), ("policy", policy), ("fraud", fraud)]:
        g.add_node(name, traced(name, fn), retry_policy=lookups)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", lambda s: ["order", "policy", "fraud"]
                            if s["claim"]["kind"] == "refund_request" else "support",
                            ["order", "policy", "fraud", "support"])
    g.add_edge(["order", "policy", "fraud"], "decide")
    g.add_edge("decide", "verify")
    g.add_conditional_edges("verify", route_after_verify,
                            ["decide", "escalate", "ask_manager", "refund", "reply"])
    g.add_edge("ask_manager", "approval")
    g.add_conditional_edges("approval", lambda s: "refund" if s["approval"] == "approved" else "reply",
                            ["refund", "reply"])
    g.add_edge("refund", "reply")
    for done in ("reply", "support", "escalate"):
        g.add_edge(done, END)

    db.parent.mkdir(parents=True, exist_ok=True)
    return g.compile(checkpointer=SqliteSaver(sqlite3.connect(db, check_same_thread=False)))


def route_after_verify(s):
    if s.get("budget") is not None and s.get("spent", 0) > s["budget"]:
        return "escalate"
    if s.get("problems"):
        return "decide" if s.get("attempts", 0) < MAX_ATTEMPTS else "escalate"
    d = s["decision"]
    if d["action"] != "refund":
        return "reply"
    return "ask_manager" if d["amount"] > APPROVAL_LIMIT else "refund"


def spec(graph):
    """Nodes and edges for the live viewer, read from LangGraph's own description of the graph."""
    drawable = graph.get_graph()
    nodes = [n for n in drawable.nodes if not n.startswith("__")]
    kinds = {"classify": "model", "decide": "model", "support": "model · loop inside",
             "approval": "person", "refund": "http · money", "order": "http", "policy": "http",
             "fraud": "http", "ask_manager": "http", "reply": "http"}
    edges = [{"from": e.source, "to": e.target, "cond": e.conditional}
             for e in drawable.edges if not e.source.startswith("__") and not e.target.startswith("__")]
    return {"entry": "classify", "nodes": [{"id": n, "kind": kinds.get(n, "code")} for n in nodes],
            "edges": edges}
