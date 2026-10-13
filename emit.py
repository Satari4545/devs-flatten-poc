from pingpong import INF, PingPong
from flatten import dissolve


def start_state(atomics):
    return {
        name: {"phase": atomic.phase, "sigma": atomic.sigma}
        for name, atomic in atomics.items()
    }


def routes_from(edges):
    routes = {}
    for src, sport, dst, dport in edges:
        routes.setdefault((src, sport), []).append((dst, dport))
    return routes


def py_value(value):
    if value == INF:
        return "INF"
    if isinstance(value, dict):
        items = [f"{key!r}: {py_value(val)}" for key, val in value.items()]
        return "{" + ", ".join(items) + "}"
    return repr(value)


def generated_source(start, routes):
    return f'''"""Generated atomic ping-pong model. Run: python3 flat_pingpong.py"""

INF = float("inf")
HIT_TIME = 0.1

# initial (phase, sigma); A starts in Send, B in Wait
START = {py_value(start)}
# (player, out-port) -> [(player, in-port)]
ROUTES = {py_value(routes)}


class FlatPingPong:
    # one atomic: time advance/output/transition functions, ports included
    def __init__(self):
        self.state = {{n: dict(s) for n, s in START.items()}}
        self.elapsed = {{n: 0.0 for n in START}}

    def time_advance(self):
        return min(s["sigma"] for s in self.state.values())

    def output_function_lambda(self):
        t = self.time_advance()
        if t == INF:
            return []
        return [(n, "send", "ball") for n, s in self.state.items()
                if s["sigma"] == t and s["phase"] == "Send"]

    def internal_transition_function(self):
        dt = self.time_advance()
        assert dt != INF, "deadlock"
        for n, s in self.state.items():
            s["sigma"] -= dt
            self.elapsed[n] += dt
        imminent = [n for n, s in self.state.items() if s["sigma"] == 0]
        bags = {{}}
        for n, port, val in self.output_function_lambda():
            for dst, dport in ROUTES.get((n, port), []):
                bags.setdefault(dst, {{}}).setdefault(dport, []).append(val)
        for n, s in self.state.items():
            bag = bags.get(n, {{}})
            if n in imminent and bag:
                self._confluent_transition_function(n, bag)  # never fires in ping pong
            elif n in imminent:
                s["phase"], s["sigma"] = "Wait", INF  # !send: hit it
                self.elapsed[n] = 0.0
            elif bag:
                self._apply_external_transition_function(n, bag)  # ?receive: caught it

    def _apply_external_transition_function(self, n, input_bag):
        if "ball" in input_bag.get("receive", []):
            self.state[n]["phase"] = "Send"
            self.state[n]["sigma"] = HIT_TIME
            self.elapsed[n] = 0.0

    def _confluent_transition_function(self, n, input_bag):
        # standard: external then internal
        self._apply_external_transition_function(n, input_bag)
        s = self.state[n]
        s["phase"], s["sigma"] = "Wait", INF
        self.elapsed[n] = 0.0

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError("closed model: no external input")

    def confluent_transition_function(self, input_bag):
        raise NotImplementedError("closed model: no external input")

    def run(self, hits=10):
        time, trace = 0.0, []
        for _ in range(hits):
            time += self.time_advance()
            trace.extend((round(time, 10), src, val)
                         for src, _, val in self.output_function_lambda())
            self.internal_transition_function()
        return trace


if __name__ == "__main__":
    for time, src, out in FlatPingPong().run():
        print(f"t={{time:g}}  {{src}} !send {{out}}")
'''


def emit(path="flat_pingpong.py"):
    atomics, edges = dissolve(PingPong())
    source = generated_source(start_state(atomics), routes_from(edges))

    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    print("wrote", path)


if __name__ == "__main__":
    emit()
