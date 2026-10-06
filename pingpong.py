"""Ping-pong: the smallest coupled model that shows why flattening matters.

Everything in this file is OURS — a demo model written for this repo.
The flattening algorithm itself lives in flatten.py.

The model: two players, one ball. Whoever holds the ball takes HIT_TIME
seconds to hit it (output "ball"), then goes quiet. Receiving the ball
makes you the holder. The coupled model below only declares structure
(who is connected to whom); it has no behavior of its own.
"""

INF = float("inf")
HIT_TIME = 1.0


class Atomic:
    """A DEVS atomic model: the smallest unit of behavior.

    Every atomic carries:
      phase  - what it is doing right now (here: "has_ball" / "no_ball")
      sigma  - time until its next internal event (INF means passive)

    and implements:
      lam()        - what it outputs when its timer runs out
      delta_int()  - how its state changes on its own timer
      delta_ext()  - how its state changes when input arrives
    """

    def __init__(self, name):
        self.name = name
        self.phase = "no_ball"
        self.sigma = INF

    def ta(self):
        return self.sigma

    def lam(self):
        raise NotImplementedError

    def delta_int(self):
        raise NotImplementedError

    def delta_ext(self, bag):
        raise NotImplementedError


class Coupled:
    """A DEVS coupled model: structure only, no behavior.

    components: name -> child model (atomic, or a nested coupled model)
    ic:         internal couplings, as (source, destination) name pairs
    """

    def __init__(self, name):
        self.name = name
        self.components = {}
        self.ic = []


class Player(Atomic):
    """One ping-pong player. Holds the ball or waits for it."""

    def __init__(self, name, has_ball):
        super().__init__(name)
        if has_ball:
            self.phase = "has_ball"
            self.sigma = HIT_TIME

    def lam(self):
        # Only the holder ever has anything to say.
        return "ball" if self.phase == "has_ball" else None

    def delta_int(self):
        # The hit: give up the ball and go passive.
        assert self.phase == "has_ball", "only the holder can hit"
        self.phase = "no_ball"
        self.sigma = INF

    def delta_ext(self, bag):
        # Catching the ball: become the holder.
        assert "ball" in bag, f"unexpected input {bag}"
        assert self.phase == "no_ball", "already holding the ball"
        self.phase = "has_ball"
        self.sigma = HIT_TIME


class PingPong(Coupled):
    """Two players wired to each other. That is the whole model."""

    def __init__(self):
        super().__init__("pingpong")
        self.components = {
            "A": Player("A", has_ball=True),
            "B": Player("B", has_ball=False),
        }
        # A hits to B, B hits to A. This is the entire structure.
        self.ic = [("A", "B"), ("B", "A")]
