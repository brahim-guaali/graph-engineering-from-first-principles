"""Stage 5 demo: a crash right after the money moves, with and without an idempotency key.

    make platform                                    # in another terminal
    python 05-side-effects/run.py                    # all scenes
    python 05-side-effects/run.py twice              # or one: twice | once | budget

    python 05-side-effects/run.py start CASE [--no-key] [--crash] [--budget USD]
    python 05-side-effects/run.py resume THREAD [--no-key]
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

from agentkit.cases import CASES
from agentkit.llm import Gemini
from agentkit.platform import Platform
from agentkit.trace import Tracer
from refund import build
from checkpoint import Checkpointer

sys.stdout.reconfigure(line_buffering=True)

STAGE = "5 · side effects"
HERE = Path(__file__).resolve().parent
cp = Checkpointer()


def graph_and_tracer(title, thread, llm, platform, use_key):
    tracer = Tracer(STAGE, title, usage=llm.usage, run_id=thread, t0=cp.started(thread))
    graph = build(llm, platform, checkpointer=cp, tracer=tracer, use_key=use_key)
    tracer.start(graph.g.spec())
    return graph, tracer


def finish(result, tracer):
    s = result["state"]
    if s["reply"] and s["reply"].get("why"):
        summary = f"escalated: {s['reply']['why']}"
    elif s["decision"]:
        summary = f"{s['decision'].action} ${s['decision'].amount:.0f} under {s['decision'].clause}"
    else:
        summary = "handed off"
    tracer.finished("done", summary)
    print(f"  ✓ done: {summary}")


def start(case_id, *flags):
    case = next(c for c in CASES if c["id"] == case_id)
    use_key = "--no-key" not in flags
    budget = float(flags[flags.index("--budget") + 1]) if "--budget" in flags else None
    thread = "t-" + uuid.uuid4().hex[:6]
    if "--crash" in flags:
        os.environ["CRASH_AFTER_PAYMENT"] = "1"
    print(f"  thread {thread}, idempotency key {'on' if use_key else 'OFF'} (pid {os.getpid()})")
    llm, platform = Gemini(), Platform()
    graph, tracer = graph_and_tracer(f"{case_id}, key {'on' if use_key else 'off'} ({thread})",
                                     thread, llm, platform, use_key)
    finish(graph.invoke({"request": case, "run_id": thread, "budget": budget}, thread=thread,
                        tracer=tracer), tracer)


def resume(thread, *flags):
    use_key = "--no-key" not in flags
    c = cp.latest(thread)
    print(f"  resuming {thread} from checkpoint {c['step']}, next {c['next']} (pid {os.getpid()})")
    llm, platform = Gemini(), Platform()
    graph, tracer = graph_and_tracer(f"{thread} (resumed)", thread, llm, platform, use_key)
    finish(graph.resume(thread, tracer=tracer), tracer)


def sub(*args, env=None):
    subprocess.run([sys.executable, str(HERE / "run.py"), *args], env=env)


def crash_and_resume(platform, use_key):
    flag = [] if use_key else ["--no-key"]
    before = set(cp.threads())
    sub("start", "missing-dessert", "--crash", *flag)
    thread = next(iter(set(cp.threads()) - before))
    print(f"  The process died. Last checkpoint: {cp.latest(thread)['step']}, next {cp.latest(thread)['next']}.")
    sub("resume", thread, *flag)
    p = platform.payments("A-8120")
    print(f"  Payments for A-8120: {p['count']}, total ${p['total']:.0f}"
          + ("   <- the customer was paid twice" if p["count"] > 1 else "   <- once, as it should be"))
    for pay in p["payments"]:
        print(f"    {pay['id']}  ${pay['amount']:.0f}  key={pay['idempotency_key']}")


def twice(platform):
    print("\n=== 1. No idempotency key: crash after paying, resume pays again ===")
    crash_and_resume(platform, use_key=False)


def once(platform):
    print("\n=== 2. With an idempotency key (run id + node): the replay is harmless ===")
    crash_and_resume(platform, use_key=True)


def budget(platform):
    print("\n=== 3. A budget in the state: an edge stops the run when the model has spent too much ===")
    sub("start", "late-grill", "--budget", "0.0002")
    print("  The run never reached the payment: the router saw spent > budget and escalated to a person.")


SCENES = {"twice": twice, "once": once, "budget": budget}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("start", "resume"):
        {"start": start, "resume": resume}[args[0]](*args[1:])
        sys.exit()
    platform = Platform()
    platform.check()
    for name in args or SCENES:
        platform.reset()
        SCENES[name](platform)
    print("""
What this stage fixed:
  - a node that touches the outside world can run twice and act once (idempotency key = run + node)
  - spending is a field in the state, so an ordinary edge can stop the run at a limit
Where it still breaks:
  1) the model's judgments are still unchecked: a wrong clause or amount goes straight to payment
  2) we still haven't measured whether the graph is actually more reliable than the loop
Next: 06-reliability adds a verifier with retries and evaluates both designs on 12 cases.""")
