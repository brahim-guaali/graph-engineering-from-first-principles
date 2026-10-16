"""A graph engine that runs in supersteps, the way Pregel (Google, 2010) does.

One superstep (round):
  1. every node that is ready runs, all at the same time (threads)
  2. each reads a snapshot of the state taken at the start of the round
  3. when all have finished, their writes are applied together, through reducers
  4. the edges of the nodes that ran decide which nodes are ready next

The API is close to LangGraph's on purpose, so stage 7 reads naturally.
"""
import copy
import time
from concurrent.futures import ThreadPoolExecutor

from agentkit.trace import NullTracer

END = "END"


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

    def compile(self):
        return Engine(self)

    def spec(self):
        """Nodes and edges as data, for the live viewer."""
        edges = [{"from": a, "to": b} for a, bs in self.edges.items() for b in bs]
        edges += [{"from": a, "to": b} for srcs, b in self.joins for a in srcs]
        edges += [{"from": a, "to": b, "label": lab, "cond": True}
                  for a, ts in self.targets.items() for b, lab in ts.items()]
        return {"entry": self.entry, "edges": [e for e in edges if e["to"] != END],
                "nodes": [{"id": n, "kind": self.kinds[n]} for n in self.nodes]}


class Engine:
    def __init__(self, graph):
        self.g = graph

    def invoke(self, inputs, log=print, tracer=None):
        self.tracer = tracer or NullTracer()
        state = {k: None for k in self.g.channels} | inputs
        self.tracer.state(state)
        active, step, waiting, t0 = [self.g.entry], 0, {}, time.monotonic()
        while active:
            step += 1
            started = time.monotonic() - t0
            writes = self.run_round(state, active, step)
            state = self.apply(state, writes)
            self.tracer.state(state)
            log(f"  round {step} [{started:5.1f}s -> {time.monotonic() - t0:5.1f}s] "
                f"{' + '.join(active):<24} wrote {sorted({k for w in writes.values() for k in w})}")
            active = self.next_nodes(state, active, waiting)
        return state

    def run_round(self, state, active, step):
        snapshot = copy.deepcopy(state)                       # what every node in this round sees
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            futures = {n: pool.submit(self.run_node, n, copy.deepcopy(snapshot), step) for n in active}
            return {n: f.result() or {} for n, f in futures.items()}   # a failure raises here

    def run_node(self, name, snapshot, step):
        self.tracer.node_started(name, round=step)
        try:
            update = self.g.nodes[name](snapshot) or {}
        except Exception as e:
            self.tracer.node_failed(name, e)
            raise
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
