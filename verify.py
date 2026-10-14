import importlib.util
import json

from pingpong import Coupled, INF, PingPong
from flatten import dissolve, FlatGraph, Resultant

TRACE_FILE = "trace.json"


class DevsSimulator:
    """Small internal simulator; the artifact is the JSON trace."""
    def __init__(self, model):
        self.model = model
        self.time = 0.0
        self.events = []

        if isinstance(model, Coupled):
            self.kind = "coupled"
            self.atomics, edges = dissolve(model)
            self.routes = {}
            for src, out_port, dst, in_port in edges:
                self.routes.setdefault((src, out_port), []).append((dst, in_port))
            self.elapsed = {name: 0.0 for name in self.atomics}
        else:
            self.kind = "atomic"

    def run(self, steps=10):
        for _ in range(steps):
            self.step()
        return self.events

    def step(self):
        self._step_coupled() if self.kind == "coupled" else self._step_atomic()

    def record(self, sender, port, value):
        self.events.append({
            "time": round(self.time, 10),
            "sender": sender,
            "port": port,
            "value": value,
        })

    def _step_atomic(self):
        dt = self.model.time_advance()
        assert dt != INF, "deadlock"
        self.time += dt
        for output in self.model.output_function_lambda():
            if len(output) == 3:
                sender, port, value = output
            else:
                port, payload = output
                sender, value = payload if isinstance(payload, tuple) else (port, payload)
            self.record(sender, port, value)
        self.model.internal_transition_function()

    def _step_coupled(self):
        dt = min(a.time_advance() for a in self.atomics.values())
        assert dt != INF, "deadlock"
        self.time += dt

        for name, atomic in self.atomics.items():
            atomic.sigma -= dt
            self.elapsed[name] += dt

        imminent = [n for n, a in self.atomics.items() if a.time_advance() == 0]
        bags = {}
        for name in imminent:
            for port, value in self.atomics[name].output_function_lambda():
                self.record(name, port, value)
                for dst, in_port in self.routes.get((name, port), []):
                    bags.setdefault(dst, {}).setdefault(in_port, []).append(value)

        for name, atomic in self.atomics.items():
            bag = bags.get(name, {})
            if name in imminent and bag:
                atomic.confluent_transition_function(bag)
            elif name in imminent:
                atomic.internal_transition_function()
            elif bag:
                atomic.external_transition_function(self.elapsed[name], bag)
            if name in imminent or bag:
                self.elapsed[name] = 0.0


def simulate(model, steps=10):
    return DevsSimulator(model).run(steps)


def reference_trace(steps=10):
    return simulate(PingPong(), steps)


def trace_json(events):
    return json.dumps(events, indent=2)


def write_reference_trace(path=TRACE_FILE, steps=10):
    events = reference_trace(steps)
    with open(path, "w", encoding="utf-8") as f:
        f.write(trace_json(events) + "\n")
    return events


def load_fused_model(path="fused_pingpong.py"):
    spec = importlib.util.spec_from_file_location("fused", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FusedPingPong()


def run_checks():
    expected = write_reference_trace()
    print(f"trace OK: wrote {TRACE_FILE}")

    atomics, edges = dissolve(PingPong())
    assert sorted(atomics) == ["A", "B"]
    assert sorted(edges) == [("A", "send", "B", "receive"),
                             ("B", "send", "A", "receive")]
    print("flatten summary inputs OK")

    assert simulate(PingPong(), 10) == expected
    print("coupled trace OK")

    atomics, edges = dissolve(PingPong())
    resultant = Resultant(FlatGraph(atomics, edges))
    assert simulate(resultant, 10) == expected
    print("flattened resultant trace OK")

    assert simulate(load_fused_model(), 10) == expected
    print("fused trace OK")


if __name__ == "__main__":
    run_checks()
