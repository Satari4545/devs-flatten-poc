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

Section 4 below is also OURS: the resultant as ONE atomic model — Hazel's
second image. The simulator talks to a single object with one ta(), one
lam(), one delta_int(); the scheduling and routing that FlatSimulator did
externally now live inside those transition functions.
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


# ---------------------------------------------------------------------------
# Section 4 — OURS (adevs has no equivalent)
#
# The resultant as ONE atomic model: Hazel's second image. The simulator
# sees a single object — one ta(), one lam(), one delta_int() — and everything
# the FlatSimulator did externally (scheduling, routing, transitions) now
# happens inside these three functions.
#
# The state is the product of the component states, exactly as the
# closure-under-coupling proof defines it (paper, Section 3) — but it is
# never enumerated. Each function computes on demand from the components,
# which is why the "insane" cartesian product is never a problem.
# ---------------------------------------------------------------------------
class Resultant(Atomic):
    def __init__(self, graph):
        super().__init__("resultant")
        self.graph = graph

    # -- the single atomic interface: everything the simulator sees --
    def ta(self):
        # Paper Section 3: the resultant's time advance is the minimum of
        # the components' time advances.
        return min(a.ta() for a in self.graph.atomics.values())

    def lam(self):
        # Pure preview: no clocks move, no transitions fire. Reports what
        # the imminent components would output.
        if self.ta() == INF:
            return []
        return [(p, a.lam()) for p, a in self.graph.atomics.items()
                if a.ta() == self.ta() and a.lam() is not None]

    def delta_int(self):
        # The entire flat-graph step, internalized: advance the clocks,
        # collect imminent outputs, route them directly, apply transitions.
        sigma = self.ta()
        assert sigma != INF, "deadlock: no atomic will ever act again"
        for a in self.graph.atomics.values():
            a.sigma -= sigma
        imminent = [p for p, a in self.graph.atomics.items() if a.ta() == 0]
        bags = {}
        for p in imminent:
            out = self.graph.atomics[p].lam()
            if out is not None:
                for d in self.graph.routes.get(p, []):
                    bags.setdefault(d, []).append(out)
        for p, a in self.graph.atomics.items():
            if p in imminent:
                a.delta_int()
            if p in bags:
                a.delta_ext(bags[p])

    def delta_ext(self, bag):
        raise NotImplementedError("ping-pong is closed: it takes no external input")
