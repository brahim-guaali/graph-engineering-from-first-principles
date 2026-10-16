"""Stream what a run is doing to the live viewer (http://127.0.0.1:8700/ui).

Every stage reports the same few events: a node started, finished or failed, a log line,
the state after a step, the run finished. Events are sent from a background thread, so
tracing never slows a run down, and if the platform isn't there they are dropped.
"""
import json
import queue
import threading
import time
import uuid

import httpx

from agentkit.platform import Platform


def preview(value, limit=400):
    """Make any state value JSON-friendly and short enough to show."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = json.dumps(str(value))
    if len(text) > limit:
        return text[:limit] + "…"
    return json.loads(text)


class Tracer:
    def __init__(self, stage, title, graph=None, usage=None, run_id=None, url=None, t0=None):
        """run_id and t0 let a resumed run, in another process, continue the same timeline."""
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.usage_of = usage                          # an llm.Usage to report alongside the state
        self.url = url or Platform().url
        self.t0 = t0 or time.time()
        self.q = queue.Queue()
        self.stage, self.title = stage, title
        threading.Thread(target=self._send_loop, daemon=True).start()
        if graph is not None:
            self.start(graph)

    # --- events --------------------------------------------------------------------
    def start(self, graph):
        """Announce the run with its graph (nodes and edges as data) so the page can draw it."""
        self.emit("run_started", stage=self.stage, title=self.title, graph=graph)

    def emit(self, type, **data):
        self.q.put({"run": self.run_id, "type": type, "t": round(time.time() - self.t0, 3), **data})

    def node_started(self, node, round=None):
        self.emit("node_started", node=node, round=round)

    def node_finished(self, node, writes=None):
        self.emit("node_finished", node=node,
                  writes={k: preview(v) for k, v in (writes or {}).items()})

    def node_failed(self, node, error):
        self.emit("node_failed", node=node, error=f"{type(error).__name__}: {error}")

    def node_waiting(self, node, why):
        self.emit("node_waiting", node=node, why=why)

    def log(self, text):
        self.emit("log", text=text)

    def state(self, state):
        self.emit("state", state={k: preview(v) for k, v in state.items() if v is not None})
        if self.usage_of is not None:
            self.usage(self.usage_of)

    def usage(self, usage):
        self.emit("usage", usage=usage.snapshot())

    def finished(self, status="done", summary=""):
        if self.usage_of is not None:
            self.usage(self.usage_of)
        self.emit("run_finished", status=status, summary=summary)
        self.flush()

    # --- delivery ------------------------------------------------------------------
    def flush(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.05)

    def _send_loop(self):
        http = httpx.Client(base_url=self.url, timeout=2)
        while True:
            batch = [self.q.get()]
            while not self.q.empty() and len(batch) < 50:
                batch.append(self.q.get_nowait())
            try:
                http.post("/events", json=batch)
            except httpx.HTTPError:
                pass                                   # the viewer is optional
            for _ in batch:
                self.q.task_done()


class NullTracer:
    """Used when nobody is watching (and in tests)."""

    run_id = "untraced"

    def __getattr__(self, name):
        return lambda *a, **k: None
