import importlib.util

from pingpong import Coupled, INF, PingPong
from flatten import dissolve, FlatGraph, Resultant
from emit import emit

EXPECTED = [(float(t), "A" if t % 2 else "B", "ball") for t in range(1, 7)]


class MiniSimulator:
    # Tiny test runner for both coupled and atomic-looking models.
    def __init__(self, model):
        self.model = model
        self.time = 0.0
        self.trace = []

        if isinstance(model, Coupled):
            self.kind = "coupled"
            self.atomics, edges = dissolve(model)
            self.routes = {}
            for src, dst in edges:
                self.routes.setdefault(src, []).append(dst)
        else:
            self.kind = "atomic"

    def run(self, steps=6):
        for _ in range(steps):
            self.step()
        return self.trace

    def step(self):
        if self.kind == "coupled":
            self._step_coupled()
        else:
            self._step_atomic()

    def _step_atomic(self):
        next_time = self.model.ta()
        assert next_time != INF, "deadlock"
        self.time += next_time
        self.trace.extend((self.time, src, out) for src, out in self.model.lam())
        self.model.delta_int()

    def _step_coupled(self):
        next_time = min(atomic.ta() for atomic in self.atomics.values())
        assert next_time != INF, "deadlock"
        self.time += next_time

        for atomic in self.atomics.values():
            atomic.sigma -= next_time

        imminent = [
            name for name, atomic in self.atomics.items()
            if atomic.ta() == 0
        ]

        bags = {}
        for name in imminent:
            out = self.atomics[name].lam()
            if out is not None:
                self.trace.append((self.time, name, out))
                for dst in self.routes.get(name, []):
                    bags.setdefault(dst, []).append(out)

        for name, atomic in self.atomics.items():
            if name in imminent:
                atomic.delta_int()
            if name in bags:
                atomic.delta_ext(bags[name])


atomics, edges = dissolve(PingPong())
assert sorted(atomics) == ["A", "B"]
assert sorted(edges) == [("A", "B"), ("B", "A")]
print("dissolve OK")

coupled_trace = MiniSimulator(PingPong()).run()
assert coupled_trace == EXPECTED
print("coupled model OK")

atomics, edges = dissolve(PingPong())
resultant = Resultant(FlatGraph(atomics, edges))
resultant_trace = MiniSimulator(resultant).run()
assert resultant_trace == coupled_trace
print("resultant matches coupled model OK")

emit("flat_pingpong.py")
spec = importlib.util.spec_from_file_location("fp", "flat_pingpong.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)
emitted_trace = MiniSimulator(fp.FlatPingPong()).run()
assert emitted_trace == coupled_trace
print("emitted file matches coupled model OK")
