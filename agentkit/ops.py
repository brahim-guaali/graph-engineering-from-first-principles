"""The operations manager: approve or reject refunds waiting on a person.

As a person, from a terminal:
    python -m agentkit.ops list
    python -m agentkit.ops approve apr_1234abcd
    python -m agentkit.ops reject apr_1234abcd

In demos, ManagerAtDesk plays the manager: it approves whatever is pending after a delay.
"""
import sys
import threading
import time

from agentkit.platform import Platform


class ManagerAtDesk:
    """A background thread that approves pending refunds after `delay` seconds."""

    def __init__(self, delay=3.0, decision="approved"):
        self.delay, self.decision = delay, decision
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()

    def _run(self):
        platform = Platform()
        seen = {}
        while not self.stop.is_set():
            for a in platform.pending_approvals():
                seen.setdefault(a["id"], time.monotonic())
                if time.monotonic() - seen[a["id"]] >= self.delay:
                    platform.decide_approval(a["id"], self.decision)
            self.stop.wait(0.5)


def main(argv):
    platform = Platform()
    if not argv or argv[0] == "list":
        for a in platform.pending_approvals():
            print(f"{a['id']}  run {a['run_id']}  order {a['order_id']}  ${a['amount']:.2f}  {a['reason']}")
        return
    action, approval_id = argv[0], argv[1]
    decision = {"approve": "approved", "reject": "rejected"}[action]
    print(platform.decide_approval(approval_id, decision, by="you"))


if __name__ == "__main__":
    main(sys.argv[1:])
