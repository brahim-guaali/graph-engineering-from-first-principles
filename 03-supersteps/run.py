"""Stage 3 demo: the refund graph in supersteps.

    make platform                                 # in another terminal
    python 03-supersteps/run.py                   # all scenes
    python 03-supersteps/run.py happy             # or one: happy | reducer | timeout
"""
import sys
import time

import httpx

from agentkit.cases import REFUND
from agentkit.llm import Gemini
from agentkit.ops import ManagerAtDesk
from agentkit.platform import Platform
from agentkit.trace import Tracer
from engine import InvalidUpdateError
from refund import CHANNELS, build

STAGE = "3 · supersteps"


def traced(title, llm, platform, **kw):
    """Build the graph and a tracer that streams the run to http://127.0.0.1:8700/ui."""
    tracer = Tracer(STAGE, title, usage=llm.usage)
    graph = build(llm, platform, tracer=tracer, **kw)
    tracer.start(graph.g.spec())
    return graph, tracer


def happy(platform):
    print("\n=== 1. Happy path, in rounds ===")
    llm = Gemini()
    t0 = time.monotonic()
    graph, tracer = traced("Happy path: $640 late catering", llm, platform)
    with ManagerAtDesk(delay=3):
        state = graph.invoke({"request": REFUND}, tracer=tracer)
    d = state["decision"]
    tracer.finished("done", f"{d.action} ${d.amount:.0f} under {d.clause}")
    print(f"  -> {d.action} ${d.amount:.0f} under {d.clause}, {time.monotonic() - t0:.1f}s, {llm.usage}")
    print("  Round 2 took as long as its slowest lookup (2 s), not the sum of all three (6 s).")
    print(f"  evidence, merged by the append reducer: {state['evidence']}")
    print(f"  spent, summed by the add reducer: ${state['spent']:.5f}")


def reducer(platform):
    print("\n=== 2. Take the reducer off 'evidence' ===")
    llm = Gemini()
    graph, tracer = traced("No reducer on 'evidence'", llm, platform, channels=dict(CHANNELS, evidence=None))
    try:
        graph.invoke({"request": REFUND}, log=lambda *_: None, tracer=tracer)
    except InvalidUpdateError as e:
        tracer.finished("failed", f"InvalidUpdateError: {e}")
        print(f"  !! InvalidUpdateError: {e}")
        print("  A loop never has to answer 'which write wins?' because only one thing runs at a time.")
        print("  A graph makes you answer it up front, and in exchange runs things in parallel safely.")


def timeout(platform):
    print("\n=== 3. The fraud service hangs in round 2 ===")
    platform.inject_faults(fraud=1)
    t0 = time.monotonic()
    llm = Gemini()
    graph, tracer = traced("The fraud service hangs", llm, platform)
    try:
        graph.invoke({"request": REFUND}, tracer=tracer)
    except httpx.TimeoutException:
        tracer.finished("failed", "fraud timed out: the whole run is lost")
        print(f"  !! the fraud node timed out at {time.monotonic() - t0:.1f}s.")
        print("  The order and policy nodes in the same round succeeded, and their results were")
        print("  thrown away with the run. The end of a round is a natural place to save...")


SCENES = {"happy": happy, "reducer": reducer, "timeout": timeout}

if __name__ == "__main__":
    platform = Platform()
    platform.check()
    for name in sys.argv[1:] or SCENES:
        platform.reset()
        SCENES[name](platform)
    print("""
What supersteps fixed:
  - fan-out and fan-in: independent lookups overlap
  - snapshot reads: nodes in one round never see each other's half-finished work,
    so the result can't depend on which node finished first
  - reducers: parallel writes to one field combine by a rule, or fail loudly
Where it still breaks:
  1) one failed node still loses the whole run, its siblings' results included
  2) still no way to stop and wait days for a person
Next: 04-checkpoints saves the state at the end of every round.""")
