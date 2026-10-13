"""Where code stops: automatic fusion, steps 1-3.

Step 1: BFS the reachable product states      -> pure code, works
Step 2: binary-encode them                    -> pure code, works
Step 3: build the transition table            -> pure code, works
Step 4: turn the table into MEANINGFUL fused
        variables (bit_0 -> `holder`, table ->
        "flip the holder")                    -> NOT code. This is the gap.

Run: python3 fuse_attempt.py
"""
import math

from pingpong import PingPong
from flatten import dissolve

atomics, edges = dissolve(PingPong())
routes = {}
for s, sp, d, dp in edges:
    routes.setdefault((s, sp), []).append((d, dp))

names = sorted(atomics)


def get_key():
    return tuple((atomics[n].phase, atomics[n].sigma) for n in names)


def set_key(k):
    for n, (ph, sg) in zip(names, k):
        atomics[n].phase, atomics[n].sigma = ph, sg


def one_step():
    dt = min(a.time_advance() for a in atomics.values())
    for a in atomics.values():
        a.sigma -= dt
    imm = [n for n in names if atomics[n].time_advance() == 0]
    outs, bags = [], {}
    for n in imm:
        for p, v in atomics[n].output_function_lambda():
            outs.append((n, p, v))
            for d, dp in routes.get((n, p), []):
                bags.setdefault(d, {}).setdefault(dp, []).append(v)
    for n in names:
        a, bag = atomics[n], bags.get(n, {})
        if n in imm and bag:
            a.confluent_transition_function(bag)
        elif n in imm:
            a.internal_transition_function()
        elif bag:
            a.external_transition_function(0.0, bag)
    return outs


# --- steps 1+2: BFS reachable states, binary-encode them ---
init = get_key()
seen = {init: 0}
queue = [init]
while queue:
    set_key(queue.pop(0))
    one_step()
    k = get_key()
    if k not in seen:
        seen[k] = len(seen)
        queue.append(k)

bits = max(1, math.ceil(math.log2(len(seen))))
print(f"STEP 1+2 (code): {len(seen)} reachable states -> {bits} bit(s)")
for k, i in sorted(seen.items(), key=lambda kv: kv[1]):
    print(f"  s{i} = {k}  ->  bit pattern {i:0{bits}b}")

# --- step 3: transition table on the encoded states ---
print("\nSTEP 3 (code): transition table")
table = {}
for k, i in seen.items():
    set_key(k)
    outs = one_step()
    j = seen[get_key()]
    table[i] = (outs, j)
    print(f"  state {i:0{bits}b}: out={outs} -> next={j:0{bits}b}")

print("\nSTEP 4 (not code): turn bit_0 into `holder`,")
print("  and 'next = 1 - state' into 'the holder flips.'")
print("  Code produced a table over bit_0. Naming what bit_0")
print("  MEANS -- who holds the ball -- is understanding,")
print("  not transforming. That is the exact breaking point.")
