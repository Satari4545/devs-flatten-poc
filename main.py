import subprocess
import sys

from pingpong import PingPong
from flatten import dissolve, FlatGraph, Resultant, show_time


def run_flatten_demo():
    print("=== running flatten demo ===")

    atomics, edges = dissolve(PingPong())
    graph = FlatGraph(atomics, edges)
    model = Resultant(graph)

    time = 0.0
    for step in range(1, 5):
        print(f"step {step}")
        dt = model.ta()
        time += dt
        print(f"time = {show_time(time)}")
        model.lam()
        model.delta_int()


def run_verify():
    print("\n=== running verify.py ===", flush=True)
    subprocess.run([sys.executable, "verify.py"], check=True)


if __name__ == "__main__":
    run_flatten_demo()
    run_verify()
