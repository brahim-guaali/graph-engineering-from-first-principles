"""Stage 2 demo: the refund as an explicit graph.

    make platform                                    # in another terminal
    python 02-explicit-graph/run.py                  # all scenes
    python 02-explicit-graph/run.py happy            # or one: happy | complaint | ownership | timeout
"""
import sys
import time

import httpx

from agentkit.cases import CASES, REFUND
from agentkit.llm import Gemini
from agentkit.ops import ManagerAtDesk
from agentkit.platform import Platform
from refund import build


def happy(platform):
    print("\n=== 1. Happy path: $640 late catering order ===")
    llm = Gemini()
    t0 = time.monotonic()
    with ManagerAtDesk(delay=3):
        state, path = build(llm, platform).run({"request": REFUND}, "classify")
    d = state["decision"]
    print(f"  -> path: {' > '.join(path)}")
    print(f"  -> {d.action} ${d.amount:.0f} under {d.clause}: {d.reason}")
    print(f"  -> {time.monotonic() - t0:.1f}s, {llm.usage}")
    print(f"  The state is data, not text: fraud score = {state['fraud']['score']}, "
          f"minutes late = {state['order']['minutes_late']}, clause = {d.clause}")


def complaint(platform):
    print("\n=== 2. A complaint takes another branch ===")
    rude = next(c for c in CASES if c["id"] == "rude-rider")
    _, path = build(Gemini(), platform).run({"request": rude}, "classify")
    print(f"  -> path: {' > '.join(path)}")


def ownership(platform):
    print("\n=== 3. A buggy node writes a field it doesn't own ===")
    g = build(Gemini(), platform)
    _, reads, writes = g.nodes["fraud"]
    g.nodes["fraud"] = (lambda s: {"fraud": {"score": 0.1}, "decision": "refund it"}, reads, writes)
    try:
        g.run({"request": REFUND}, "classify", log=lambda *_: None)
    except PermissionError as e:
        print(f"  !! {e}")
        print("  Ownership turns a silent bug (the fraud check deciding the refund) into a loud one.")


def timeout(platform):
    print("\n=== 4. The fraud service hangs ===")
    platform.inject_faults(fraud=1)
    t0 = time.monotonic()
    try:
        build(Gemini(), platform).run({"request": REFUND}, "classify")
    except httpx.TimeoutException:
        print(f"  !! the fraud node timed out at {time.monotonic() - t0:.1f}s. We know exactly which node")
        print("  failed and what the state held, but the state lived in this process. It's gone.")


SCENES = {"happy": happy, "complaint": complaint, "ownership": ownership, "timeout": timeout}

if __name__ == "__main__":
    platform = Platform()
    platform.check()
    for name in sys.argv[1:] or SCENES:
        platform.reset()
        SCENES[name](platform)
    print("""
What the graph fixed:
  - the state is named fields; each node reads only what it needs
  - step order and the $500 rule are code; the model makes 2 narrow judgments with typed answers
  - a bug or a failure points at one node
Where it still breaks:
  1) one node at a time: the three lookups still run in series
  2) a crash still loses the run: the state only lives in memory
  3) the approval still blocks: no way to wait days for a person
Next: 03-supersteps runs every ready node at once.""")
