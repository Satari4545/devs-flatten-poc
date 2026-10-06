"""Prove the flattened model behaves exactly like the original.

1. Dissolve the coupled model and check the structure (2 atomics, A<->B).
2. Run the flat graph through the external loop (Hazel's image 1).
3. Drive the Resultant through the single atomic interface (Hazel's image 2):
   one ta()/lam()/delta_int(), 6 steps, ball alternates A, B, A, B...
4. Emit the standalone file and check its FlatPingPong gives the same trace.
"""

import importlib.util

from pingpong import PingPong, INF
from flatten import dissolve, FlatGraph, FlatSimulator, Resultant
from emit import emit

EXPECTED = [(float(t), ("A" if t % 2 else "B"), "ball") for t in range(1, 7)]

atomics, edges = dissolve(PingPong())
assert sorted(atomics) == ["A", "B"], atomics.keys()
assert sorted(edges) == [("A", "B"), ("B", "A")], edges

graph = FlatGraph(atomics, edges)
trace = FlatSimulator(graph).run(6)
assert trace == EXPECTED, trace
print("image 1 (flat graph + external loop) OK")

atomics2, _ = dissolve(PingPong())
resultant = Resultant(FlatGraph(atomics2, [("A", "B"), ("B", "A")]))
time, rtrace = 0.0, []
for _ in range(6):
    assert resultant.ta() != INF
    time += resultant.ta()
    rtrace.extend((time, p, o) for p, o in resultant.lam())
    resultant.delta_int()
assert rtrace == EXPECTED, rtrace
print("image 2 (single atomic interface) OK")

emit("flat_pingpong.py")
spec = importlib.util.spec_from_file_location("flat_pingpong", "flat_pingpong.py")
flat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flat)
assert flat.FlatPingPong().run() == EXPECTED
print("emitted standalone atomic model OK: identical trace")

print("all checks passed")
