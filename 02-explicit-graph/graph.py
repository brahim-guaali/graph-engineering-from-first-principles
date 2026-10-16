"""Stage 2: write the structure down as a graph.

Three parts and nothing else:
  * state: named fields, written down before any prompt
  * nodes: one job each; a node reads some fields and may only write the ones it owns
  * edges: what may run next, either fixed, or conditional (plain code where code can decide)

The runner is deliberately simple: one node at a time.
"""
import time

from agentkit.trace import NullTracer

END = "END"


class Graph:
    def __init__(self, fields):
        self.fields = fields     # {name: description}: the state, designed first
        self.nodes = {}          # name -> (fn, reads, writes)
        self.kinds = {}          # name -> model | http | person | code (for the viewer)
        self.edges = {}          # name -> next node
        self.branches = {}       # name -> router(state) -> next node
        self.targets = {}        # name -> {next node: label}, where a router may go

    def node(self, name, reads=(), writes=(), kind="code"):
        def register(fn):
            unknown = (set(reads) | set(writes)) - set(self.fields)
            assert not unknown, f"{name} uses fields that aren't in the state: {unknown}"
            self.nodes[name] = (fn, tuple(reads), tuple(writes))
            self.kinds[name] = kind
            return fn
        return register

    def edge(self, src, dst):
        self.edges[src] = dst

    def branch(self, src, router, targets):
        self.branches[src] = router
        self.targets[src] = targets

    def spec(self):
        """Nodes and edges as data, for the live viewer."""
        edges = [{"from": a, "to": b} for a, b in self.edges.items() if b != END]
        edges += [{"from": a, "to": b, "label": lab, "cond": True}
                  for a, ts in self.targets.items() for b, lab in ts.items()]
        return {"nodes": [{"id": n, "kind": self.kinds[n]} for n in self.nodes], "edges": edges}

    def run(self, inputs, start, log=print, tracer=None):
        tracer = tracer or NullTracer()
        state = {f: inputs.get(f) for f in self.fields}
        tracer.state(state)
        path, current, t0 = [], start, time.monotonic()
        while current != END:
            fn, reads, writes = self.nodes[current]
            started = time.monotonic() - t0
            tracer.node_started(current, round=len(path) + 1)
            try:
                update = fn({k: state[k] for k in reads}) or {}     # a node sees only what it reads
                illegal = set(update) - set(writes)
                if illegal:
                    raise PermissionError(f"node '{current}' wrote {sorted(illegal)}, "
                                          f"but it only owns {list(writes)}")
            except Exception as e:
                tracer.node_failed(current, e)
                raise
            tracer.node_finished(current, update)
            state.update(update)
            tracer.state(state)
            path.append(current)
            log(f"  [{started:5.1f}s -> {time.monotonic() - t0:5.1f}s] {current:<9} wrote {list(update)}")
            current = self.branches[current](state) if current in self.branches else self.edges[current]
        return state, path
