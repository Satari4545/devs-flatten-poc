INF = float("inf")
HIT_TIME = 0.1


# Tiny DEVS base class: state + five DEVS functions.
class Atomic:
    def __init__(self, name):
        self.name = name
        self.in_ports = set()
        self.out_ports = set()
        self.phase = "Wait"
        self.sigma = INF  # time remaining until this model's next event

    def add_in_port(self, port):
        self.in_ports.add(port)

    def add_out_port(self, port):
        self.out_ports.add(port)

    def time_advance(self):
        return self.sigma

    # lambda/output: called right before an internal transition.
    def output_function_lambda(self):
        raise NotImplementedError

    def internal_transition_function(self):
        raise NotImplementedError

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError

    def confluent_transition_function(self, input_bag):
        # If input and internal event happen together, do external then internal.
        self.external_transition_function(self.time_advance(), input_bag)
        self.internal_transition_function()


# Coupled model: components plus port-to-port internal couplings.
class Coupled:
    def __init__(self, name):
        self.name = name
        self.components = {}
        self.ic = []  # (src, out_port, dst, in_port)


# Player has two phases: Send for 0.1 time units, then Wait forever.
class Player(Atomic):
    def __init__(self, name, starts_with_ball):
        super().__init__(name)
        self.add_in_port("receive")
        self.add_out_port("send")
        if starts_with_ball:
            self.phase = "Send"
            self.sigma = HIT_TIME

    def output_function_lambda(self):
        return [("send", "ball")] if self.phase == "Send" else []

    def internal_transition_function(self):
        self.phase = "Wait"
        self.sigma = INF

    def external_transition_function(self, elapsed_time, input_bag):
        if "ball" in input_bag.get("receive", []):
            self.phase = "Send"
            self.sigma = HIT_TIME


# A starts ready to send; sends are routed to the other player's receive port.
class PingPong(Coupled):
    def __init__(self):
        super().__init__("pingpong")
        self.components = {"A": Player("A", True), "B": Player("B", False)}
        self.ic = [("A", "send", "B", "receive"),
                   ("B", "send", "A", "receive")]
