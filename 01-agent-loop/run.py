"""Stage 1 demo: the naive agent loop on the $640 late-catering refund.

    make platform                                # in another terminal
    python 01-agent-loop/run.py                  # all scenes
    python 01-agent-loop/run.py happy            # or one: happy | timeout | away
"""
import json
import sys
import time

import httpx

from agent import ManagerAway, agent_loop
from agentkit.cases import REFUND
from agentkit.llm import Gemini
from agentkit.ops import ManagerAtDesk
from agentkit.platform import Platform


def happy(platform):
    print("\n=== 1. Happy path: the manager is at their desk ===")
    llm = Gemini()
    t0 = time.monotonic()
    with ManagerAtDesk(delay=3):
        answer, transcript, trace = agent_loop(REFUND, llm)
    print(f"\n  -> {answer.strip()[:300]}")
    print(f"  -> {time.monotonic() - t0:.1f}s, {llm.usage}")
    print(f"  -> money moved: {platform.payments('A-7731')['total']}")
    size = len(json.dumps([c.model_dump(mode='json', exclude_none=True) for c in transcript]))
    print(f"\n  Its state is a {size:,}-character transcript of {len(transcript)} messages.")
    print("  'What was the fraud score when it decided?' means searching that transcript.")


def timeout(platform):
    print("\n=== 2. The fraud service hangs once ===")
    platform.inject_faults(fraud=1)
    llm = Gemini()
    t0 = time.monotonic()
    try:
        with ManagerAtDesk(delay=3):
            agent_loop(REFUND, llm)
    except httpx.TimeoutException:
        lost = time.monotonic() - t0
        print(f"  !! the fraud call timed out. The run is gone after {lost:.1f}s and {llm.usage.calls} model calls.")
        print("  The order and policy it already fetched only lived in that run's transcript.")
        print("  There is no saved position, so the only option is to start over:")
        with ManagerAtDesk(delay=3):
            agent_loop(REFUND, llm, log=lambda *_: None)
        print(f"  -> total {time.monotonic() - t0:.1f}s and {llm.usage} for one refund.")


def away(platform):
    print("\n=== 3. The manager is out until Thursday ===")
    try:
        agent_loop(REFUND, Gemini(), approval_wait=15)
    except ManagerAway as e:
        print(f"  !! {e}.")
        print("  The loop can only wait for a person by blocking. It holds the process, the")
        print("  connection and the whole context open until Thursday, or it gives up. We gave up.")
        print(f"  Approval still pending on the platform: {len(platform.pending_approvals())}")


SCENES = {"happy": happy, "timeout": timeout, "away": away}

if __name__ == "__main__":
    platform = Platform()
    platform.check()
    for name in sys.argv[1:] or SCENES:
        platform.reset()
        SCENES[name](platform)
    print("""
Where the loop breaks. It isn't the model, it's the structure:
  1) serial: the tool calls run one after another, even the independent ones
  2) all-or-nothing: one timeout throws away every finished step
  3) no pause: a person in the process means blocking for days or giving up
  4) state is a transcript: every call re-reads all of it, and nothing has a name
Next: 02-explicit-graph writes the structure down.""")
