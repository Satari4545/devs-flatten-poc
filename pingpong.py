INF = float("inf")
HIT_TIME = 1.0


class Atomic:
    # the smallest unit of behavior: a phase, a timer, and four functions
    def __init__(self, name):
        self.name = name
        self.phase = "no_ball"
        self.sigma = INF  # time until my next event; INF means passive

    def ta(self):
        return self.sigma

    def lam(self):
        raise NotImplementedError

    def delta_int(self):
        raise NotImplementedError

    def delta_ext(self, bag):
        raise NotImplementedError


class Coupled:
    # structure only: children + who talks to whom. no behavior of its own.
    def __init__(self, name):
        self.name = name
        self.components = {}
        self.ic = []


class Player(Atomic):
    # one ping-pong player. holds the ball, or waits for it.
    def __init__(self, name, has_ball):
        super().__init__(name)
        if has_ball:
            self.phase = "has_ball"
            self.sigma = HIT_TIME

    def lam(self):
        return "ball" if self.phase == "has_ball" else None

    def delta_int(self):
        self.phase = "no_ball"  # hit it: give it up, go quiet
        self.sigma = INF

    def delta_ext(self, bag):
        self.phase = "has_ball"  # caught it: start the timer
        self.sigma = HIT_TIME


class PingPong(Coupled):
    # two players wired to each other. that's the whole model.
    def __init__(self):
        super().__init__("pingpong")
        self.components = {"A": Player("A", True), "B": Player("B", False)}
        self.ic = [("A", "B"), ("B", "A")]
