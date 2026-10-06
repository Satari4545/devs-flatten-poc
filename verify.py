"""Prove the flattened model behaves exactly like the original.

1. Dissolve the coupled model, run the flat simulator, check the ball
   alternates A, B, A, B... one hit per second.
2. Emit the standalone file, import it, and check its trace is identical.
"""

import importlib.util

from pingpong import PingPong
from flatten import dissolve, FlatGraph, FlatSimulator
from emit import emit

EXPECTED = [(float(t), ("A" if t % 2 else "B"), "ball") for t in range(1, 7)]

atomics, edges = dissolve(PingPong())
assert sorted(atomics) == ["A", "B"], atomics.keys()
assert sorted(edges) == [("A", "B"), ("B", "A")], edges

trace = FlatSimulator(FlatGraph(atomics, edges)).run(6)
assert trace == EXPECTED, trace
print("flat simulator trace OK:", [(t, s) for t, s, _ in trace])

emit("flat_pingpong.py")
spec = importlib.util.spec_from_file_location("flat_pingpong", "flat_pingpong.py")
flat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flat)
assert flat.run() == EXPECTED, flat.run()
print("emitted standalone model trace OK: identical")

print("all checks passed")
