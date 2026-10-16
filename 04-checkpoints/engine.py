"""The superstep engine from stage 3, plus a checkpoint at the end of every round.

What changes (diff this file against ../03-supersteps/engine.py):
  * after each round the whole state, the nodes due next and the join progress go to SQLite
  * if a node fails, its siblings' finished writes are saved; resume() re-runs only the failure
  * a node can call interrupt(payload): the run stops cleanly and the process may exit;
    resume(thread, value) continues days later, in another process, with value as the answer
  * fork() copies an old checkpoint with some fields changed and runs forward from there
"""
import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait

from agentkit.trace import NullTracer
from checkpoint import Checkpointer

END = "END"
_local = threading.local()


class GraphInterrupt(Exception):
    """Raised by interrupt() to stop the run until someone answers."""

    def __init__(self, payload):
        super().__init__(str(payload))
        self.payload = payload


def interrupt(payload):
    """Pause the run here. On resume, the node runs again from the top and this returns the answer."""
    if getattr(_local, "resume", None) is not None:
        return _local.resume
    raise GraphInterrupt(payload)


class InvalidUpdateError(Exception):
    """Several nodes wrote one field in the same round and the field has no reducer."""


def append(old, new):
    return (old or []) + (new if isinstance(new, list) else [new])


def add(old, new):
    return (old or 0) + new


class StateGraph:
    def __init__(self, channels):
        self.channels = channels      # {field: reducer or None}
        self.nodes = {}
        self.kinds = {}               # name -> model | http | person | code (for the viewer)
        self.edges = {}               # src -> [dst, ...]: several = fan-out
        self.joins = []               # ([src, ...], dst): dst waits for every src (fan-in)
        self.branches = {}            # src -> router(state) -> name or [names]
        self.targets = {}             # src -> {dst: label}: where a router may go (for drawing)
        self.entry = None

    def add_node(self, name, fn, kind="code"):
        self.nodes[name] = fn
        self.kinds[name] = kind

    def add_edge(self, src, dst):
        if isinstance(src, (list, tuple)):
            self.joins.append((tuple(src), dst))
        else:
            self.edges.setdefault(src, []).append(dst)

    def add_conditional_edges(self, src, router, targets):
        self.branches[src] = router
        self.targets[src] = targets if isinstance(targets, dict) else {t: "" for t in targets}

    def set_entry(self, name):
        self.entry = name

    def compile(self, checkpointer=None):
        return Engine(self, checkpointer or Checkpointer())

    def spec(self):
        """Nodes and edges as data, for the live viewer."""
        edges = [{"from": a, "to": b} for a, bs in self.edges.items() for b in bs]
        edges += [{"from": a, "to": b} for srcs, b in self.joins for a in srcs]
        edges += [{"from": a, "to": b, "label": lab, "cond": True}
                  for a, ts in self.targets.items() for b, lab in ts.items()]
        return {"entry": self.entry, "edges": [e for e in edges if e["to"] != END],
                "nodes": [{"id": n, "kind": self.kinds[n]} for n in self.nodes]}


class Engine:
    def __init__(self, graph, checkpointer):
        self.g, self.cp = graph, checkpointer
        self.tracer = NullTracer()

    def invoke(self, inputs, thread=None, log=print, tracer=None):
        """Start a new run (a thread). Returns {"status", "thread", "state", "interrupt"}."""
        thread = thread or uuid.uuid4().hex[:8]
        state = {k: None for k in self.g.channels} | inputs
        self.cp.save(thread, 0, state, [self.g.entry], {})
        return self._run(thread, state, [self.g.entry], {}, 0, None, log, tracer)

    def resume(self, thread, value=None, log=print, tracer=None):
        """Carry on from the latest checkpoint, in this process or any other."""
        c = self.cp.latest(thread)
        if c["status"] == "done":
            return {"status": "done", "thread": thread, "state": c["state"], "interrupt": None}
        return self._run(thread, c["state"], c["next"], c["waiting"], c["step"], value, log, tracer)

    def fork(self, thread, step, updates):
        """Time travel: copy the checkpoint after `step`, change some fields, and get a new thread."""
        c = self.cp.at(thread, step)
        new = f"{thread}-fork-{uuid.uuid4().hex[:4]}"
        self.cp.save(new, step, c["state"] | updates, c["next"], c["waiting"],
                     note=f"forked from {thread} at step {step} with {sorted(updates)} changed")
        return new

    def _run(self, thread, state, active, waiting, step, resume, log, tracer):
        self.tracer = tracer or NullTracer()
        self.tracer.state(state)
        t0 = time.monotonic()
        while active:
            step += 1
            started = time.monotonic() - t0
            saved = self.cp.saved_writes(thread, step)      # from an earlier attempt at this round
            if saved:
                log(f"  round {step}: reusing saved results of {sorted(saved)}")
            todo = [n for n in active if n not in saved]
            writes, errors = self.run_round(state, todo, step, resume)
            for node, update in writes.items():             # keep every finished node's work
                self.cp.save_write(thread, step, node, update)
            writes = saved | writes

            interrupts = [e for e in errors.values() if isinstance(e, GraphInterrupt)]
            if interrupts:
                payload = interrupts[0].payload
                self.cp.save(thread, step - 1, state, active, waiting, "interrupted", str(payload))
                log(f"  round {step} [{started:5.1f}s] paused: {payload}")
                return {"status": "interrupted", "thread": thread, "state": state, "interrupt": payload}
            if errors:
                node, error = next(iter(errors.items()))
                self.cp.save(thread, step - 1, state, active, waiting, "failed", f"{node}: {error!r}")
                raise error

            resume = None                                   # an answer is used once
            state = self.apply(state, writes)
            active = self.next_nodes(state, active, waiting)
            self.cp.save(thread, step, state, active, waiting, "running" if active else "done")
            self.tracer.state(state)
            log(f"  round {step} [{started:5.1f}s -> {time.monotonic() - t0:5.1f}s] "
                f"{' + '.join(sorted(writes)):<24} checkpoint {step} saved")
        return {"status": "done", "thread": thread, "state": state, "interrupt": None}

    def run_round(self, state, active, step, resume):
        """Run the round's nodes in parallel; return (writes of those that finished, errors)."""
        if not active:
            return {}, {}
        snapshot = copy.deepcopy(state)
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            futures = {n: pool.submit(self.run_node, n, copy.deepcopy(snapshot), step, resume)
                       for n in active}
            wait(futures.values())                          # let siblings finish even if one fails
        writes, errors = {}, {}
        for n, f in futures.items():
            if f.exception() is None:
                writes[n] = f.result() or {}
            else:
                errors[n] = f.exception()
        return writes, errors

    def run_node(self, name, snapshot, step, resume):
        _local.resume = resume
        self.tracer.node_started(name, round=step)
        try:
            update = self.g.nodes[name](snapshot) or {}
        except GraphInterrupt as e:
            self.tracer.node_waiting(name, str(e.payload))
            raise
        except Exception as e:
            self.tracer.node_failed(name, e)
            raise
        finally:
            _local.resume = None
        self.tracer.node_finished(name, update)
        return update

    def apply(self, state, writes):
        by_field = {}
        for node, update in writes.items():
            for field, value in update.items():
                if field not in self.g.channels:
                    raise KeyError(f"node '{node}' wrote unknown field '{field}'")
                by_field.setdefault(field, []).append((node, value))
        state = dict(state)
        for field, values in by_field.items():
            reducer = self.g.channels[field]
            if reducer is None:
                if len(values) > 1:
                    raise InvalidUpdateError(
                        f"'{field}' got {len(values)} writes in one round (from "
                        f"{[n for n, _ in values]}) and has no reducer to combine them")
                state[field] = values[0][1]
            else:
                for _, value in values:
                    state[field] = reducer(state[field], value)
        return state

    def next_nodes(self, state, ran, waiting):
        ready = []
        for node in ran:
            ready += self.g.edges.get(node, [])
            if node in self.g.branches:
                chosen = self.g.branches[node](state)
                ready += chosen if isinstance(chosen, list) else [chosen]
            for sources, dst in self.g.joins:
                if node in sources:
                    seen = waiting.setdefault(dst, set())
                    seen.add(node)
                    if seen >= set(sources):
                        ready.append(dst)
                        del waiting[dst]
        return [n for n in dict.fromkeys(ready) if n != END]
