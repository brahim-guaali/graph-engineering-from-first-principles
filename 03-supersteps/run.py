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
from engine import InvalidUpdateError
from refund import CHANNELS, build


def happy(platform):
    print("\n=== 1. Happy path, in rounds ===")
    llm = Gemini()
    t0 = time.monotonic()
    with ManagerAtDesk(delay=3):
        state = build(llm, platform).invoke({"request": REFUND})
    d = state["decision"]
    print(f"  -> {d.action} ${d.amount:.0f} under {d.clause}, {time.monotonic() - t0:.1f}s, {llm.usage}")
    print("  Round 2 took as long as its slowest lookup (2 s), not the sum of all three (6 s).")
    print(f"  evidence, merged by the append reducer: {state['evidence']}")
    print(f"  spent, summed by the add reducer: ${state['spent']:.5f}")


def reducer(platform):
    print("\n=== 2. Take the reducer off 'evidence' ===")
    try:
        build(Gemini(), platform, dict(CHANNELS, evidence=None)).invoke(
            {"request": REFUND}, log=lambda *_: None)
    except InvalidUpdateError as e:
        print(f"  !! InvalidUpdateError: {e}")
        print("  A loop never has to answer 'which write wins?' because only one thing runs at a time.")
        print("  A graph makes you answer it up front, and in exchange runs things in parallel safely.")


def timeout(platform):
    print("\n=== 3. The fraud service hangs in round 2 ===")
    platform.inject_faults(fraud=1)
    t0 = time.monotonic()
    try:
        build(Gemini(), platform).invoke({"request": REFUND})
    except httpx.TimeoutException:
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
