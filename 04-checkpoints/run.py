"""Stage 4 demo: checkpoints. Every scene uses real, separate processes where it matters.

    make platform                                   # in another terminal
    python 04-checkpoints/run.py                    # all scenes
    python 04-checkpoints/run.py crash              # or one: crash | pause | kill | travel

    python 04-checkpoints/run.py start [case]       # start one run; it exits when it pauses
    python 04-checkpoints/run.py resume THREAD      # carry on (checks the manager's answer)
    python 04-checkpoints/run.py history THREAD     # every checkpoint of a run
"""
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from agentkit.cases import CASES, REFUND
from agentkit.llm import Gemini
from agentkit.platform import Platform
from agentkit.trace import Tracer
from checkpoint import Checkpointer
from refund import build

sys.stdout.reconfigure(line_buffering=True)        # keep our lines in order with the subprocesses

STAGE = "4 · checkpoints"
HERE = Path(__file__).resolve().parent
PY = sys.executable
cp = Checkpointer()


def graph_and_tracer(title, thread, llm, platform):
    tracer = Tracer(STAGE, title, usage=llm.usage, run_id=thread, t0=cp.started(thread))
    graph = build(llm, platform, checkpointer=cp, tracer=tracer)
    tracer.start(graph.g.spec())
    return graph, tracer


def report(result, tracer):
    if result["status"] == "interrupted":
        tracer.finished("paused", f"waiting for {result['interrupt']['approval_id']}: process can exit")
        print(f"  ⏸ paused: {result['interrupt']['question']}  ({result['interrupt']['approval_id']})")
    else:
        d = result["state"]["decision"]
        summary = f"{d.action} ${d.amount:.0f} under {d.clause}" if d else "handed off"
        tracer.finished("done", summary)
        print(f"  ✓ done: {summary}")


# --- commands (each one is a fresh process when the scenes call them) -----------------
def start(case_id="late-catering"):
    case = next(c for c in CASES if c["id"] == case_id)
    thread = "t-" + uuid.uuid4().hex[:6]
    print(f"  thread {thread} (pid {os.getpid()})", flush=True)
    llm, platform = Gemini(), Platform()
    graph, tracer = graph_and_tracer(f"{case['id']} ({thread})", thread, llm, platform)
    try:
        result = graph.invoke({"request": case}, thread=thread, tracer=tracer)
    except Exception as e:
        tracer.finished("failed", f"{type(e).__name__}: {e} (checkpoint kept)")
        print(f"  ✕ {type(e).__name__}: {e}")
        return thread
    report(result, tracer)
    print(f"  {llm.usage}")
    return thread


def resume(thread):
    c = cp.latest(thread)
    llm, platform = Gemini(), Platform()
    value = None
    if c["status"] == "interrupted":
        approval_id = c["state"]["approval_request"]["id"]
        value = platform.get_approval(approval_id)["status"]
        if value == "pending":
            print(f"  still waiting for {approval_id}. Nothing is running meanwhile.")
            return
        print(f"  {approval_id} was {value}: resuming {thread} from checkpoint {c['step']} (pid {os.getpid()})")
    else:
        print(f"  resuming {thread} from checkpoint {c['step']} ({c['status']}: {c['note']}) (pid {os.getpid()})")
    graph, tracer = graph_and_tracer(f"{thread} (resumed)", thread, llm, platform)
    report(graph.resume(thread, value, tracer=tracer), tracer)
    print(f"  {llm.usage} in this process")


def history(thread):
    for c in cp.history(thread):
        filled = [k for k, v in c["state"].items() if v is not None and k != "request"]
        print(f"  checkpoint {c['step']}: {c['status']:<11} next={c['next']}  state has {filled}"
              + (f"  ({c['note']})" if c["note"] else ""))


def sub(*args):
    """Run a command of this file in a new process and stream its output."""
    subprocess.run([PY, str(HERE / "run.py"), *args], check=True)


# --- scenes ---------------------------------------------------------------------------
def crash(platform):
    print("\n=== 1. The fraud service hangs: resume re-runs only the failed node ===")
    platform.inject_faults(fraud=1)
    thread = start()
    print("  The run failed, but round 2's finished work was saved:")
    print(f"  saved writes for round 2: {sorted(cp.saved_writes(thread, 2))}")
    history(thread)
    print("\n  Resume in a new process: classify is not called again; order and policy are reused.")
    sub("resume", thread)
    approve_and_resume(platform, thread)
    return thread


def pause(platform):
    print("\n=== 2. Waiting for the manager: the process exits and nothing is held open ===")
    sub("start")
    thread = cp.threads()[0]
    c = cp.latest(thread)
    print(f"  The process has exited. The run is a row in SQLite: checkpoint {c['step']}, {c['status']}.")
    print("  ...it's Thursday. The manager approves from their own terminal:")
    approval_id = c["state"]["approval_request"]["id"]
    subprocess.run([PY, "-m", "agentkit.ops", "approve", approval_id], check=True, capture_output=True)
    print(f"  $ python -m agentkit.ops approve {approval_id}")
    sub("resume", thread)


def kill(platform):
    print("\n=== 3. kill -9 in the middle of a run, then resume in a fresh process ===")
    before = set(cp.threads())
    proc = subprocess.Popen([PY, str(HERE / "run.py"), "start", "missing-dessert"])
    thread = None
    while thread is None or (cp.latest(thread) or {}).get("step", 0) < 2:   # wait for round 2
        time.sleep(0.2)
        new = set(cp.threads()) - before
        thread = next(iter(new), None)
    time.sleep(0.5)                                     # now inside round 3 (the model call)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    print(f"  💥 killed pid {proc.pid} during round 3. Last checkpoint: {cp.latest(thread)['step']}")
    sub("resume", thread)
    print(f"  Money moved for A-8120: ${platform.payments('A-8120')['total']:.0f} (once)")


def travel(platform, thread):
    print("\n=== 4. Time travel: load an old checkpoint, change one field, run forward ===")
    llm = Gemini()
    new = build(llm, platform, checkpointer=cp).fork(thread, 2, {"fraud": {"customer": "C-1042", "score": 0.92,
                                           "signals": ["what if this account looked risky?"]}})
    print(f"  forked {thread} after round 2 into {new} with fraud score 0.92")
    graph, tracer = graph_and_tracer(f"{new}: what if fraud were 0.92?", new, llm, platform)
    result = graph.resume(new, tracer=tracer)
    report(result, tracer)
    print(f"  Only decide and reply ran: {llm.usage}. Same order, same policy, a different answer.")


def approve_and_resume(platform, thread):
    c = cp.latest(thread)
    if c["status"] == "interrupted":
        approval_id = c["state"]["approval_request"]["id"]
        platform.decide_approval(approval_id, "approved")
        print(f"  (manager approves {approval_id})")
        sub("resume", thread)


if __name__ == "__main__":
    args = sys.argv[1:]
    commands = {"start": start, "resume": resume, "history": history}
    if args and args[0] in commands:
        commands[args[0]](*args[1:])
        sys.exit()
    platform = Platform()
    platform.check()
    platform.reset()
    scenes = args or ["crash", "pause", "kill", "travel"]
    thread = None
    for name in scenes:
        if name == "crash":
            thread = crash(platform)
        elif name == "travel":
            travel(platform, thread or crash(platform))
        else:
            {"pause": pause, "kill": kill}[name](platform)
    print("""
What checkpoints fixed:
  - a failure costs one node: the rest of the round and every earlier round are kept
  - a person is a node: the run pauses, the process exits, and it resumes days later anywhere
  - a crash (even kill -9) loses at most the round in flight
  - an audit trail (history) and time travel (fork an old checkpoint, change a field)
Where it still breaks:
  1) a node that pays and then crashes before its checkpoint is re-run on resume: paid twice
  2) nothing stops a run that spends too much
Next: 05-side-effects adds idempotency keys and a budget.""")
