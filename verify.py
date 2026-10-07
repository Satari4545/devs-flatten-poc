from flat_pingpong import FlatPingPong
from pingpong import INF, PingPong


def run_coupled(model, steps=6):
    time = 0.0
    trace = []
    atomics = model.components
    routes = {}

    for src, dst in model.ic:
        routes.setdefault(src, []).append(dst)

    for _ in range(steps):
        next_time = min(atomic.ta() for atomic in atomics.values())
        assert next_time != INF, "deadlock"
        time += next_time

        for atomic in atomics.values():
            atomic.sigma -= next_time

        imminent = [
            name for name, atomic in atomics.items()
            if atomic.ta() == 0
        ]

        bags = {}
        for src in imminent:
            output = atomics[src].lam()
            if output is not None:
                trace.append((time, src, output))
                for dst in routes.get(src, []):
                    bags.setdefault(dst, []).append(output)

        for name, atomic in atomics.items():
            if name in imminent:
                atomic.delta_int()
            if name in bags:
                atomic.delta_ext(bags[name])

    return trace


coupled_trace = run_coupled(PingPong())
flat_trace = FlatPingPong().run()

print("pingpong.py output:")
for event in coupled_trace:
    print(" ", event)

print("flat_pingpong.py output:")
for event in flat_trace:
    print(" ", event)

assert flat_trace == coupled_trace
print("OK: flat_pingpong.py matches pingpong.py")
