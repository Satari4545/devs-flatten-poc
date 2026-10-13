"""Fused ping-pong: the resultant after fusion, derived by hand.

The fusion, step by step:
  1. Invariant: exactly one player holds the ball, so (phase_A, phase_B)
     collapses to one variable `holder`. 4 combos -> 2 states.
  2. The holder's timer is always 0.1, the waiter's always inf, so one
     timer suffices instead of two.
  3. Hit + route + catch becomes: flip the holder.

Result: no loops over components, no routing table, no sub-objects.
One state variable, one timer, one ta, one lam, one delta_int.
X = {} (closed model); Y = !send carrying (holder, ball).

This was derived by a human spotting the invariant. Automating that
step in general is undecidable -- it is the LLM fusion pass.
"""

INF = float("inf")
HIT_TIME = 0.1


class FusedPingPong:
    def __init__(self):
        self.holder = "A"  # who is in Send; the other is in Wait
        self.sigma = HIT_TIME

    def time_advance(self):
        return self.sigma

    def output_function_lambda(self):
        return [("send", (self.holder, "ball"))]

    def internal_transition_function(self):
        self.holder = "B" if self.holder == "A" else "A"
        self.sigma = HIT_TIME

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError("closed model: no external input")

    def confluent_transition_function(self, input_bag):
        raise NotImplementedError("closed model: no external input")

    def run(self, hits=10):
        time, trace = 0.0, []
        for _ in range(hits):
            time += self.time_advance()
            trace.extend((round(time, 10), holder, val)
                         for _, (holder, val) in self.output_function_lambda())
            self.internal_transition_function()
        return trace


if __name__ == "__main__":
    for t, src, out in FusedPingPong().run():
        print(f"t={t:g}  {src} !send {out}")
