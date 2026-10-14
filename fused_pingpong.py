INF = float("inf")
HIT_TIME = 0.1


class FusedPingPong:
    def __init__(self):
        # Invariant: exactly one player holds the ball.
        self.holder = "A"
        self.sigma = HIT_TIME

    def time_advance(self):
        return self.sigma

    def output_function_lambda(self):
        # Normal atomic output: (port, value). Value carries the sender.
        return [("send", (self.holder, "ball"))]

    def internal_transition_function(self):
        # Sending the ball just flips the holder.
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
            trace.extend((round(time, 10), holder, value)
                         for _port, (holder, value) in self.output_function_lambda())
            self.internal_transition_function()
        return trace


if __name__ == "__main__":
    for time, sender, value in FusedPingPong().run():
        print(f"t={time:g}  {sender} !send {value}")
