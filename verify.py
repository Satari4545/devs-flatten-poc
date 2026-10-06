# proves all three agree: the flat-graph loop, the resultant, the emitted file.
import importlib.util

from pingpong import PingPong
from flatten import dissolve, FlatGraph, FlatSimulator, Resultant
from emit import emit

EXPECTED = [(float(t), "A" if t % 2 else "B", "ball") for t in range(1, 7)]

atomics, edges = dissolve(PingPong())
assert sorted(atomics) == ["A", "B"]
assert sorted(edges) == [("A", "B"), ("B", "A")]
assert FlatSimulator(FlatGraph(atomics, edges)).run(6) == EXPECTED
print("flat graph OK")

atomics, edges = dissolve(PingPong())
r = Resultant(FlatGraph(atomics, edges))
t, trace = 0.0, []
for _ in range(6):
    t += r.ta()
    trace += [(t, p, o) for p, o in r.lam()]
    r.delta_int()
assert trace == EXPECTED
print("resultant OK")

emit("flat_pingpong.py")
spec = importlib.util.spec_from_file_location("fp", "flat_pingpong.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)
assert fp.FlatPingPong().run() == EXPECTED
print("emitted file OK")
