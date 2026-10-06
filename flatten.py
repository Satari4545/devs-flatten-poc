from pingpong import Atomic, Coupled, INF


def dissolve(model):
    # from adevs (models.h, Coupled::assign_to_graph):
    # walk the hierarchy once. every atomic gets a dotted path,
    # every coupling becomes a direct atomic-to-atomic edge.
    atomics, edges = {}, []

    def rec(node, prefix):
        here = {}
        for name, child in node.components.items():
            path = prefix + name
            if isinstance(child, Coupled):
                here[name] = rec(child, path + ".")
            else:
                atomics[path] = child
                here[name] = [path]
        for src, dst in node.ic:
            for s in here[src]:
                for d in here[dst]:
                    edges.append((s, d))
        return [p for paths in here.values() for p in paths]

    rec(model, "")
    return atomics, edges


class FlatGraph:
    # from adevs (graph.h): the flat model. atomics + who sends to whom.
    def __init__(self, atomics, edges):
        self.atomics = atomics
        self.routes = {}
        for src, dst in edges:
            self.routes.setdefault(src, []).append(dst)


class FlatSimulator:
    # from adevs (simulator.h): the event loop over the flat graph.
    # one step: smallest timer wins, imminents output, outputs get routed,
    # transitions apply.
    # (ping-pong never has a component that's both imminent and receiving,
    # so there's no confluent case here; a general simulator would need one.)
    def __init__(self, graph):
        self.graph = graph
        self.time = 0.0
        self.trace = []

    def step(self):
        atomics = self.graph.atomics
        sigma = min(a.ta() for a in atomics.values())
        assert sigma != INF, "deadlock"
        self.time += sigma
        for a in atomics.values():
            a.sigma -= sigma
        imminent = [p for p, a in atomics.items() if a.ta() == 0]
        bags = {}
        for p in imminent:
            out = atomics[p].lam()
            if out is not None:
                self.trace.append((self.time, p, out))
                for d in self.graph.routes.get(p, []):
                    bags.setdefault(d, []).append(out)
        for p, a in atomics.items():
            if p in imminent:
                a.delta_int()
            if p in bags:
                a.delta_ext(bags[p])

    def run(self, steps):
        for _ in range(steps):
            self.step()
        return self.trace


class Resultant(Atomic):
    # ours: the flat graph as ONE atomic. the simulator sees a single
    # ta/lam/delta_int; the loop above now lives inside these functions.
    # nothing is precomputed: each function works it out on demand.
    def __init__(self, graph):
        super().__init__("resultant")
        self.graph = graph

    def ta(self):
        return min(a.ta() for a in self.graph.atomics.values())

    def lam(self):
        if self.ta() == INF:
            return []
        return [(p, a.lam()) for p, a in self.graph.atomics.items()
                if a.ta() == self.ta() and a.lam() is not None]

    def delta_int(self):
        sigma = self.ta()
        assert sigma != INF, "deadlock"
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
        raise NotImplementedError("ping-pong takes no external input")
