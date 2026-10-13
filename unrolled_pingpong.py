"""The paper's resultant, unrolled to Player-style by hand.

This is the middle rung:
  1. flatten.py Resultant  = the paper, faithfully (delegates to components)
  2. THIS FILE             = paper + unroll product + inline transitions
  3. fused_pingpong.py     = this + invariant -> `holder` (insight)

What a code translator would need for step 1->2:
  - unroll the product tuple into named variables      (mechanical)
  - INLINE each component's transition code into the
    resultant's functions, with routing resolved        (soft break:
    partial evaluation of arbitrary Python -- worked here
    only because Player's functions are 3 straight lines
    and a human did it)
  - case-split over imminent subsets (2^n cases)       (mechanical,
    but exponential)

What it would need for 2->3: discovering that
(phase_A, phase_B) is always one of two combos and naming
the collapsed variable `holder`. Hard break: insight.
"""
INF = float("inf")
HIT_TIME = 0.1


class UnrolledPingPong:
    def __init__(self):
        # product state, unrolled: 4 variables, no component objects
        self.phase_A = "Send"
        self.sigma_A = HIT_TIME
        self.phase_B = "Wait"
        self.sigma_B = INF

    def time_advance(self):
        # paper: "the minimum of the component time advances"
        return min(self.sigma_A, self.sigma_B)

    def output_function_lambda(self):
        # A's lam inlined, gated on A imminent; same for B
        out = []
        if self.sigma_A == 0 and self.phase_A == "Send":
            out.append(("send", ("A", "ball")))
        if self.sigma_B == 0 and self.phase_B == "Send":
            out.append(("send", ("B", "ball")))
        return out

    def internal_transition_function(self):
        # inlined by hand: hit + route + catch, one case per
        # imminent subset (2^2 = 4 cases; 2 reachable here)
        if self.sigma_A == 0 and self.sigma_B == 0:
            raise NotImplementedError("confluent case never occurs here")
        elif self.sigma_A == 0:
            # A's delta_int inlined ...
            self.phase_A, self.sigma_A = "Wait", INF
            # ... routed A!send -> B?receive, B's delta_ext inlined
            self.phase_B, self.sigma_B = "Send", HIT_TIME
        elif self.sigma_B == 0:
            self.phase_B, self.sigma_B = "Wait", INF
            self.phase_A, self.sigma_A = "Send", HIT_TIME

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError("closed model: no external input")

    def confluent_transition_function(self, input_bag):
        raise NotImplementedError("closed model: no external input")

    def run(self, hits=10):
        time, trace = 0.0, []
        for _ in range(hits):
            dt = self.time_advance()
            time += dt
            self.sigma_A -= dt  # clocks advance, like the simulator does
            self.sigma_B -= dt
            trace.extend((round(time, 10), holder, val)
                         for _, (holder, val) in self.output_function_lambda())
            self.internal_transition_function()
        return trace


if __name__ == "__main__":
    for t, src, out in UnrolledPingPong().run():
        print(f"t={t:g}  {src} !send {out}")
