INF = float("inf")
HIT_TIME = 0.1  # Send lasts 0.1 time units, like the DEVS graph


class Atomic:
    def __init__(self, name):
        self.name = name
        self.in_ports = set()
        self.out_ports = set()
        self.phase = "Wait"
        self.sigma = INF  # time until next internal event

    def add_in_port(self, port):
        self.in_ports.add(port)

    def add_out_port(self, port):
        self.out_ports.add(port)

    def time_advance(self):
        return self.sigma

    def output_function_lambda(self):
        raise NotImplementedError  # -> [(port, value)]

    def internal_transition_function(self):
        raise NotImplementedError

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError  # input_bag: {port: [values]}

    def confluent_transition_function(self, input_bag):
        # standard confluent: external (at elapsed_time = time_advance) then internal
        self.external_transition_function(self.time_advance(), input_bag)
        self.internal_transition_function()


class Coupled:
    def __init__(self, name):
        self.name = name
        self.components = {}
        # (src, out_port, dst, in_port)
        self.ic = []


class Player(Atomic):
    # Send (time_advance=0.1) --!send--> Wait (time_advance=inf) --?receive--> Send
    def __init__(self, name, starts_with_ball):
        super().__init__(name)
        self.add_in_port("receive")
        self.add_out_port("send")
        if starts_with_ball:
            self.phase = "Send"
            self.sigma = HIT_TIME

    def output_function_lambda(self):
        if self.phase == "Send":
            return [("send", "ball")]
        return []

    def internal_transition_function(self):
        self.phase = "Wait"  # !send: hit it, go quiet
        self.sigma = INF

    def external_transition_function(self, elapsed_time, input_bag):
        if "ball" in input_bag.get("receive", []):
            self.phase = "Send"  # ?receive: caught it
            self.sigma = HIT_TIME


class PingPong(Coupled):
    def __init__(self):
        super().__init__("pingpong")
        self.components = {"A": Player("A", True), "B": Player("B", False)}
        self.ic = [("A", "send", "B", "receive"),
                   ("B", "send", "A", "receive")]
