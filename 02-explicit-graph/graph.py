"""Stage 2: write the structure down as a graph.

Three parts and nothing else:
  * state: named fields, written down before any prompt
  * nodes: one job each; a node reads some fields and may only write the ones it owns
  * edges: what may run next, either fixed, or conditional (plain code where code can decide)

The runner is deliberately simple: one node at a time.
"""
import time

END = "END"


class Graph:
    def __init__(self, fields):
        self.fields = fields     # {name: description}: the state, designed first
        self.nodes = {}          # name -> (fn, reads, writes)
        self.edges = {}          # name -> next node
        self.branches = {}       # name -> router(state) -> next node

    def node(self, name, reads=(), writes=()):
        def register(fn):
            unknown = (set(reads) | set(writes)) - set(self.fields)
            assert not unknown, f"{name} uses fields that aren't in the state: {unknown}"
            self.nodes[name] = (fn, tuple(reads), tuple(writes))
            return fn
        return register

    def edge(self, src, dst):
        self.edges[src] = dst

    def branch(self, src, router):
        self.branches[src] = router

    def run(self, inputs, start, log=print):
        state = {f: inputs.get(f) for f in self.fields}
        path, current, t0 = [], start, time.monotonic()
        while current != END:
            fn, reads, writes = self.nodes[current]
            started = time.monotonic() - t0
            update = fn({k: state[k] for k in reads}) or {}     # a node sees only what it reads
            illegal = set(update) - set(writes)
            if illegal:
                raise PermissionError(f"node '{current}' wrote {sorted(illegal)}, "
                                      f"but it only owns {list(writes)}")
            state.update(update)
            path.append(current)
            log(f"  [{started:5.1f}s -> {time.monotonic() - t0:5.1f}s] {current:<9} wrote {list(update)}")
            current = self.branches[current](state) if current in self.branches else self.edges[current]
        return state, path
