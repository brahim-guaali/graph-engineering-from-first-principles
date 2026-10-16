"""Stage 7 demo: the refund graph on LangGraph.

    make platform                                 # in another terminal
    python 07-langgraph/run.py                    # all scenes
    python 07-langgraph/run.py happy              # or one: happy | retry | support | travel
    python 07-langgraph/run.py mermaid            # print the graph as a Mermaid diagram
"""
import sys
import uuid

from langgraph.types import Command

from agentkit.cases import CASES, REFUND
from agentkit.llm import Gemini
from agentkit.platform import Platform
from agentkit.trace import Tracer
from graph import build, spec

sys.stdout.reconfigure(line_buffering=True)
STAGE = "7 · langgraph"


def start(title, platform):
    llm = Gemini()
    thread = "lg-" + uuid.uuid4().hex[:6]
    tracer = Tracer(STAGE, title, usage=llm.usage, run_id=thread)
    graph = build(llm, platform, tracer)
    tracer.start(spec(graph))
    return graph, tracer, {"configurable": {"thread_id": thread}}, llm


def summary(state):
    d = state.get("decision")
    if state.get("reply", {}).get("ticket"):
        t = state["reply"]["ticket"]
        return f"ticket {t['id']}: {t['category']}, {t['priority']}: {t['summary']}"
    return f"{d['action']} ${d['amount']:g} under {d['clause']}" if d else str(state.get("reply"))


def happy(platform):
    print("\n=== 1. $640 late catering: fan-out, verify, pause for the manager, resume ===")
    graph, tracer, config, llm = start("Happy path: $640 late catering", platform)
    state = graph.invoke({"request": REFUND}, config)
    pending = graph.get_state(config)
    print(f"  paused before {pending.next}: {pending.interrupts[0].value['question']}")
    tracer.finished("paused", "waiting for the operations manager")
    platform.decide_approval(pending.interrupts[0].value["approval_id"], "approved")
    state = graph.invoke(Command(resume="approved"), config)       # any process, any time later
    tracer.finished("done", summary(state))
    print(f"  ✓ {summary(state)}; evidence {state['evidence']}; {llm.usage}")
    history = list(graph.get_state_history(config))
    print(f"  {len(history)} checkpoints in SQLite, e.g. next={history[-2].next} then {history[-3].next}")


def retry(platform):
    print("\n=== 2. The fraud service hangs: a RetryPolicy on the node absorbs it ===")
    platform.inject_faults(fraud=1)
    small = next(c for c in CASES if c["id"] == "missing-dessert")
    graph, tracer, config, llm = start("Fraud hangs once: RetryPolicy", platform)
    state = graph.invoke({"request": small}, config)
    tracer.finished("done", summary(state))
    print(f"  ✓ {summary(state)}. The run never failed: the fraud node was retried by the framework.")


def support(platform):
    print("\n=== 3. The hybrid: a complaint goes to a node with an agent loop inside ===")
    rude = next(c for c in CASES if c["id"] == "rude-rider")
    graph, tracer, config, llm = start("A complaint: loop inside a node", platform)
    state = graph.invoke({"request": rude}, config)
    tracer.finished("done", summary(state))
    print(f"  ✓ {summary(state)}")
    print(f"  reply: {state['reply']['text'][:200]}")


def travel(platform):
    print("\n=== 4. Time travel with update_state: what if the fraud score had been 0.92? ===")
    small = next(c for c in CASES if c["id"] == "late-grill")
    graph, tracer, config, llm = start("late-grill, then a fork", platform)
    state = graph.invoke({"request": small}, config)
    tracer.finished("done", summary(state))
    print(f"  original: {summary(state)}")
    before = next(s for s in graph.get_state_history(config) if s.next == ("decide",))
    fork = graph.update_state(before.config, {"fraud": {"score": 0.92, "signals": ["what if?"]}},
                              as_node="fraud")
    state = graph.invoke(None, fork)
    print(f"  fork:     {summary(state)}  (only decide, verify and reply ran again)")


SCENES = {"happy": happy, "retry": retry, "support": support, "travel": travel}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["mermaid"]:
        print(build(None, None).get_graph().draw_mermaid())
        sys.exit()
    platform = Platform()
    platform.check()
    for name in args or SCENES:
        platform.reset()
        SCENES[name](platform)
    print("""
Everything we built by hand exists in the framework, under the same names. What the framework
can't decide for you is the design: the state, which judgments go to a model, which edges are
code, where a person sits, which nodes touch money. That is graph engineering.""")
