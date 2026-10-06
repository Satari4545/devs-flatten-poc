# pingpong-flatten

The smallest possible coupled-to-atomic flattening demo, written so every
line can be explained. Two ping-pong players, one ball, ~200 lines total.

## Run it

```
python3 verify.py     # dissolve -> simulate -> emit -> check the artifact
python3 emit.py       # just write out flat_pingpong.py
python3 flat_pingpong.py   # run the standalone flattened model
```

## The files

| File | What it is | Where the idea comes from |
|---|---|---|
| `pingpong.py` | The demo coupled model (2 players) | Ours |
| `flatten.py` §1 `dissolve()` | Recursive hierarchy dissolution | Adapted from adevs `Coupled::assign_to_graph()` (`include/adevs/models.h`) |
| `flatten.py` §2 `FlatGraph` | Flat components + direct routing table | Adapted from adevs `include/adevs/graph.h` |
| `flatten.py` §3 `FlatSimulator` | Event loop over the flat graph | Adapted from adevs `include/adevs/simulator.h` |
| `flatten.py` §4 `Resultant` | The flat graph as ONE atomic model (single ta/lam/delta_int) | Ours — this is Hazel's "second image"; adevs stops at the flat graph |
| `emit.py` → `flat_pingpong.py` | The resultant as a standalone file | Ours — adevs has no equivalent |

"Adapted" is stated honestly: adevs is C++, this is a simplified Python
reimplementation of the same algorithm, not a verbatim copy.

## The one-paragraph explanation

A coupled model is just structure: components plus couplings. Flattening
walks that structure once (`dissolve`), gives every atomic a dotted path,
and rewrites every coupling as a direct atomic-to-atomic edge. What remains
is a flat graph — no hierarchy — which the simulator runs by repeating one
rule: the smallest timer wins, imminents output, outputs route directly,
transitions apply. `Resultant` takes the last step Hazel asked for: it wraps
the flat graph in a single atomic interface, so the simulator talks to one
object and the scheduling/routing live inside its transition functions.
That wrapper *is* the closure-under-coupling resultant evaluated on demand,
which is why the cartesian product of state spaces is never enumerated.
`emit.py` then writes the whole thing out as a single file: one atomic class
holding the initial states as plain data, the routing table, and the same
transition functions. That last step — spitting out the resultant instead of
only simulating it — is what adevs doesn't do, and what this repo adds.

## The repo this came from

adev's flattening lives at [github.com/smiz/adevs](https://github.com/smiz/adevs):
`include/adevs/models.h`, `include/adevs/graph.h`, `include/adevs/simulator.h`.
