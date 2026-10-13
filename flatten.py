from pingpong import Atomic, Coupled, INF


def show_time(value):
    return "INF" if value == INF else f"{value:g}"


def dissolve(model):
    print("dissolve: coupled model -> atomics + edges")

    atomics = {}
    edges = []  # (src_path, src_port, dst_path, dst_port)

    def walk(node, prefix=""):
        children = {}

        for name, child in node.components.items():
            path = prefix + name
            if isinstance(child, Coupled):
                print(f"  enter coupled {path}")
                children[name] = walk(child, path + ".")
            else:
                atomics[path] = child
                children[name] = [path]
                print(
                    f"  atomic {path}: "
                    f"phase={child.phase}, sigma={show_time(child.sigma)}, "
                    f"in={sorted(child.in_ports)}, out={sorted(child.out_ports)}"
                )

        for sname, sport, dname, dport in node.ic:
            for spath in children[sname]:
                for dpath in children[dname]:
                    edges.append((spath, sport, dpath, dport))
                    print(f"  edge {spath}:{sport} -> {dpath}:{dport}")

        return [path for paths in children.values() for path in paths]

    walk(model)
    print(f"  atomics = {list(atomics)}")
    print(f"  edges = {edges}\n")
    return atomics, edges


class FlatGraph:
    def __init__(self, atomics, edges):
        print("FlatGraph: edges -> routes")

        self.atomics = atomics
        self.routes = {}  # (src, port) -> [(dst, port)]

        for src, sport, dst, dport in edges:
            self.routes.setdefault((src, sport), []).append((dst, dport))

        print(f"  routes = {self.routes}\n")


class Resultant(Atomic):
    def __init__(self, graph):
        print("Resultant: flat graph -> one atomic model\n")
        super().__init__("resultant")
        self.graph = graph
        self._elapsed = {name: 0.0 for name in graph.atomics}
        # visited states only: never the full product
        self._cache = {}

    def _key(self):
        # (phase, sigma, elapsed) per atomic is the whole global state
        return tuple(
            (name, atomic.phase, atomic.sigma, self._elapsed[name])
            for name, atomic in sorted(self.graph.atomics.items())
        )

    def _snapshot(self):
        return {
            name: {"phase": atomic.phase, "sigma": atomic.sigma,
                   "elapsed": self._elapsed[name]}
            for name, atomic in self.graph.atomics.items()
        }

    def _restore(self, snap):
        for name, atomic in self.graph.atomics.items():
            atomic.phase = snap[name]["phase"]
            atomic.sigma = snap[name]["sigma"]
            self._elapsed[name] = snap[name]["elapsed"]

    def _show_state(self):
        for name, atomic in self.graph.atomics.items():
            print(f"  {name}: phase={atomic.phase}, "
                  f"sigma={show_time(atomic.sigma)}")

    def time_advance(self):
        next_time = min(atomic.time_advance() for atomic in self.graph.atomics.values())
        print(f"time_advance() -> {show_time(next_time)}")
        return next_time

    def output_function_lambda(self):
        next_time = min(atomic.time_advance() for atomic in self.graph.atomics.values())
        outputs = [
            (name, port, value)
            for name, atomic in self.graph.atomics.items()
            if atomic.time_advance() == next_time
            for port, value in atomic.output_function_lambda()
        ]
        print(f"output_function_lambda() -> {outputs}")
        return outputs

    def internal_transition_function(self):
        key = self._key()
        hit = self._cache.get(key)
        if hit is not None:
            print(f"internal_transition_function(): cache hit ({len(self._cache)} states seen)")
            self._restore(hit)
            self._show_state()
            print()
            return

        next_time = min(atomic.time_advance() for atomic in self.graph.atomics.values())
        assert next_time != INF, "deadlock"

        print(f"internal_transition_function(): advance {show_time(next_time)}")

        atomics = self.graph.atomics
        for name, atomic in atomics.items():
            atomic.sigma -= next_time
            self._elapsed[name] += next_time

        imminent = [
            name for name, atomic in atomics.items()
            if atomic.time_advance() == 0
        ]
        print(f"  imminent = {imminent}")

        bags = {}
        for name, atomic in atomics.items():
            if atomic.time_advance() == 0:
                for port, value in atomic.output_function_lambda():
                    for dst, dport in self.graph.routes.get((name, port), []):
                        bags.setdefault(dst, {}).setdefault(dport, []).append(value)
        print(f"  routed inputs = {bags}")

        for name, atomic in atomics.items():
            bag = bags.get(name, {})
            if name in imminent and bag:
                atomic.confluent_transition_function(bag)  # never fires in ping pong
            elif name in imminent:
                atomic.internal_transition_function()
            elif bag:
                atomic.external_transition_function(self._elapsed[name], bag)
            if name in imminent or bag:
                self._elapsed[name] = 0.0

        self._show_state()
        print()

        self._cache[key] = self._snapshot()

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError("ping-pong takes no external input")

    def confluent_transition_function(self, input_bag):
        raise NotImplementedError("ping-pong takes no external input")
