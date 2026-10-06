"""Minimal coupled-to-flat DEVS flattening.

Where the algorithm comes from: adevs (github.com/smiz/adevs), James Nutaro's
DEVS simulator. adevs dissolves a coupled-model hierarchy into a flat graph of
atomics so it can simulate without walking the hierarchy. This file is a
simplified Python reimplementation of that same algorithm — not a verbatim
copy (adevs is C++), but each section below does the same job as the adevs
file named in its header comment.

  Section 1: dissolve()
      ADAPTED FROM adevs include/adevs/models.h, Coupled::assign_to_graph().
      Recursively walks the hierarchy. Every atomic ends up with a dotted
      path ("A", or "Table.A" if nested); every coupling is rewritten as a
      direct atomic-to-atomic edge.

  Section 2: FlatGraph
      ADAPTED FROM adevs include/adevs/graph.h.
      The flattened model: components plus a direct routing table that says
      who sends to whom. No hierarchy remains.

  Section 3: FlatSimulator
      ADAPTED FROM adevs include/adevs/simulator.h.
      Runs the flat graph: the smallest timer wins, imminent components
      output, outputs route directly, transitions apply. This is the
      closure-under-coupling resultant evaluated on demand — the transition
      functions are derived from the components, so the cartesian product
      of state spaces is never enumerated.

What adevs does NOT do (and this repo adds in emit.py): write the flattened
model out as a standalone file. adevs flattens only inside its own simulator
to run faster; the flat graph never leaves it.
"""

from pingpong import Atomic, Coupled, INF


# ---------------------------------------------------------------------------
# Section 1 — ADAPTED FROM adevs include/adevs/models.h
# (Coupled::assign_to_graph())
#
# Walk the hierarchy exactly once. Two things come out:
#   atomics: dotted path -> the atomic component itself
#   edges:   (source_path, dest_path) direct routes, no hierarchy involved
# ---------------------------------------------------------------------------
def dissolve(model):
    atomics = {}
    edges = []

    def rec(node, prefix):
        # Flat atomic paths reachable under this node, per child name.
        here = {}
        for name, child in node.components.items():
            path = prefix + name
            if isinstance(child, Coupled):
                # Nested coupled model: dissolve it too, prefixing its paths
                # so "A" inside "Table" becomes "Table.A".
                here[name] = rec(child, path + ".")
            else:
                atomics[path] = child
                here[name] = [path]
        # Rewrite this level's couplings as direct atomic-to-atomic edges.
        # A coupling (src, dst) between children becomes every combination
        # of flat sources under src x flat destinations under dst.
        for src, dst in node.ic:
            for s in here[src]:
                for d in here[dst]:
                    edges.append((s, d))
        # Hand our flat paths up so the parent level can route through us.
        return [p for paths in here.values() for p in paths]

    rec(model, "")
    return atomics, edges


# ---------------------------------------------------------------------------
# Section 2 — ADAPTED FROM adevs include/adevs/graph.h
#
# The flattened model. Components plus a routing table: for each atomic,
# the list of atomics its output goes to directly.
# ---------------------------------------------------------------------------
class FlatGraph:
    def __init__(self, atomics, edges):
        self.atomics = atomics            # path -> atomic component
        self.routes = {}                  # path -> [destination paths]
        for src, dst in edges:
            self.routes.setdefault(src, []).append(dst)


# ---------------------------------------------------------------------------
# Section 3 — ADAPTED FROM adevs include/adevs/simulator.h
#
# The event loop over the flat graph. One step is the whole algorithm:
#   1. sigma = smallest timer across atomics  (the next event time)
#   2. advance every clock by sigma
#   3. imminent atomics (timer == 0) produce output
#   4. route each output directly to its destinations' input bags
#   5. destinations take external transitions; imminents take internal ones
#
# Simplification, stated honestly: ping-pong can never have a component
# that is both imminent AND receiving in the same step, so there is no
# confluent (delta_con) case here. A general simulator would need it.
# ---------------------------------------------------------------------------
class FlatSimulator:
    def __init__(self, graph):
        self.graph = graph
        self.time = 0.0
        self.trace = []                   # (time, source, output) per hit

    def step(self):
        atomics = self.graph.atomics
        sigma = min(a.ta() for a in atomics.values())
        assert sigma != INF, "deadlock: no atomic will ever act again"
        self.time += sigma
        for a in atomics.values():
            a.sigma -= sigma

        # 3. Who acts now?
        imminent = [p for p, a in atomics.items() if a.ta() == 0]

        # 4. Their outputs, routed directly (no hierarchy to walk).
        bags = {}
        for p in imminent:
            out = atomics[p].lam()
            if out is not None:
                self.trace.append((self.time, p, out))
                for d in self.graph.routes.get(p, []):
                    bags.setdefault(d, []).append(out)

        # 5. Transitions.
        for p, a in atomics.items():
            if p in imminent:
                a.delta_int()
            if p in bags:
                a.delta_ext(bags[p])

    def run(self, steps):
        for _ in range(steps):
            self.step()
        return self.trace
