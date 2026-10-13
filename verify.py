import importlib.util
import os
import subprocess

from pingpong import Coupled, INF, PingPong
from flatten import dissolve, FlatGraph, Resultant
from emit import emit

# t=0.1 A sends, t=0.2 B sends, ... (matches cpp/pingpong adevs run)
EXPECTED = [(round(0.1 * t, 10), "A" if t % 2 == 1 else "B", "ball")
            for t in range(1, 11)]


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
            for src, sport, dst, dport in edges:
                self.routes.setdefault((src, sport), []).append((dst, dport))
            self.elapsed = {name: 0.0 for name in self.atomics}
        else:
            self.kind = "atomic"

    def run(self, steps=10):
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
        self.trace.extend((round(self.time, 10), src, val)
                          for src, _, val in self.model.lam())
        self.model.delta_int()

    def _step_coupled(self):
        next_time = min(a.ta() for a in self.atomics.values())
        assert next_time != INF, "deadlock"
        self.time += next_time

        for name, a in self.atomics.items():
            a.sigma -= next_time
            self.elapsed[name] += next_time

        imminent = [n for n, a in self.atomics.items() if a.ta() == 0]

        bags = {}
        for name in imminent:
            for port, val in self.atomics[name].lam():
                self.trace.append((round(self.time, 10), name, val))
                for dst, dport in self.routes.get((name, port), []):
                    bags.setdefault(dst, {}).setdefault(dport, []).append(val)

        for name, a in self.atomics.items():
            bag = bags.get(name, {})
            if name in imminent and bag:
                a.delta_conf(bag)
            elif name in imminent:
                a.delta_int()
            elif bag:
                a.delta_ext(self.elapsed[name], bag)
            if name in imminent or bag:
                self.elapsed[name] = 0.0


def cpp_trace():
    # trace from the real adevs C++ run; None if it can't be built/run
    exe = os.path.join("cpp", "pingpong")
    if not os.path.exists(exe):
        if subprocess.run(["make", "-C", "cpp"],
                           capture_output=True).returncode != 0:
            return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True,
                             check=True).stdout
    except Exception:
        return None
    trace, t = [], None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("t="):
            t = round(float(line[2:]), 10)
        elif line.endswith("!send ball"):
            trace.append((t, line.split()[0], "ball"))
    return trace


atomics, edges = dissolve(PingPong())
assert sorted(atomics) == ["A", "B"]
assert sorted(edges) == [("A", "send", "B", "receive"),
                         ("B", "send", "A", "receive")]
print("dissolve OK")

coupled_trace = MiniSimulator(PingPong()).run()
assert coupled_trace == EXPECTED, coupled_trace
print("coupled model OK")

atomics, edges = dissolve(PingPong())
resultant = Resultant(FlatGraph(atomics, edges))
resultant_trace = MiniSimulator(resultant).run()
assert resultant_trace == coupled_trace, resultant_trace
print("resultant matches coupled model OK")

emit("flat_pingpong.py")
spec = importlib.util.spec_from_file_location("fp", "flat_pingpong.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)
emitted_trace = MiniSimulator(fp.FlatPingPong()).run()
assert emitted_trace == coupled_trace, emitted_trace
print("emitted file matches coupled model OK")

ct = cpp_trace()
if ct is None:
    print("C++ adevs check skipped (could not build/run cpp/pingpong)")
else:
    assert ct == EXPECTED, ct
    print("C++ adevs trace matches OK")
