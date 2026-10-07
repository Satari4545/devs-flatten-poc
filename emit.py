from pingpong import INF, PingPong
from flatten import dissolve


def start_state(atomics):
    return {
        name: {"phase": atomic.phase, "sigma": atomic.sigma}
        for name, atomic in atomics.items()
    }


def routes_from(edges):
    routes = {}
    for src, dst in edges:
        routes.setdefault(src, []).append(dst)
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
HIT_TIME = 1.0

START = {py_value(start)}
ROUTES = {py_value(routes)}


class FlatPingPong:
    def __init__(self):
        self.state = {{name: dict(state) for name, state in START.items()}}

    def ta(self):
        return min(state["sigma"] for state in self.state.values())

    def lam(self):
        next_time = self.ta()
        if next_time == INF:
            return []
        return [
            (name, "ball")
            for name, state in self.state.items()
            if state["sigma"] == next_time and state["phase"] == "has_ball"
        ]

    def delta_int(self):
        next_time = self.ta()
        assert next_time != INF, "deadlock"

        for state in self.state.values():
            state["sigma"] -= next_time

        senders = [
            name for name, state in self.state.items()
            if state["sigma"] == 0 and state["phase"] == "has_ball"
        ]
        receivers = {{dst for src in senders for dst in ROUTES.get(src, [])}}

        for name in senders:
            self.state[name] = {{"phase": "no_ball", "sigma": INF}}
        for name in receivers:
            self.state[name] = {{"phase": "has_ball", "sigma": HIT_TIME}}

    def run(self, hits=6):
        time = 0.0
        trace = []
        for _ in range(hits):
            time += self.ta()
            trace.extend((time, src, out) for src, out in self.lam())
            self.delta_int()
        return trace


if __name__ == "__main__":
    for time, src, out in FlatPingPong().run():
        print(f"t={{time:g}}  {{src}} -> {{out}}")
'''


def emit(path="flat_pingpong.py"):
    atomics, edges = dissolve(PingPong())
    source = generated_source(start_state(atomics), routes_from(edges))

    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    print("wrote", path)


if __name__ == "__main__":
    emit()
