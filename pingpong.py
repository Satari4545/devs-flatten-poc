INF = float("inf")
HIT_TIME = 0.1  # Send lasts 0.1 time units, like the DEVS graph


class Atomic:
    # proper DEVS atomic: ports + ta/lam/delta_int/delta_ext/delta_conf
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

    def ta(self):
        return self.sigma

    def lam(self):
        raise NotImplementedError  # -> [(port, value)]

    def delta_int(self):
        raise NotImplementedError

    def delta_ext(self, e, bag):
        raise NotImplementedError  # bag: {port: [values]}

    def delta_conf(self, bag):
        # standard confluent: external (at e = ta) then internal
        self.delta_ext(self.ta(), bag)
        self.delta_int()


class Coupled:
    def __init__(self, name):
        self.name = name
        self.components = {}
        # (src, out_port, dst, in_port)
        self.ic = []


class Player(Atomic):
    # Send (ta=0.1) --!send--> Wait (ta=inf) --?receive--> Send
    def __init__(self, name, starts_with_ball):
        super().__init__(name)
        self.add_in_port("receive")
        self.add_out_port("send")
        if starts_with_ball:
            self.phase = "Send"
            self.sigma = HIT_TIME

    def lam(self):
        if self.phase == "Send":
            return [("send", "ball")]
        return []

    def delta_int(self):
        self.phase = "Wait"  # !send: hit it, go quiet
        self.sigma = INF

    def delta_ext(self, e, bag):
        if "ball" in bag.get("receive", []):
            self.phase = "Send"  # ?receive: caught it
            self.sigma = HIT_TIME


class PingPong(Coupled):
    def __init__(self):
        super().__init__("pingpong")
        self.components = {"A": Player("A", True), "B": Player("B", False)}
        self.ic = [("A", "send", "B", "receive"),
                   ("B", "send", "A", "receive")]
