from pingpong import Atomic, Coupled, INF


def show_time(value):
    return "INF" if value == INF else f"{value:g}"


def dissolve(model):
    print("dissolve: coupled model -> atomics + edges")

    atomics = {}
    edges = []

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
                    f"phase={child.phase}, sigma={show_time(child.sigma)}"
                )

        for src, dst in node.ic:
            for src_path in children[src]:
                for dst_path in children[dst]:
                    edges.append((src_path, dst_path))
                    print(f"  edge {src_path} -> {dst_path}")

        return [path for paths in children.values() for path in paths]

    walk(model)
    print(f"  atomics = {list(atomics)}")
    print(f"  edges = {edges}\n")
    return atomics, edges


class FlatGraph:
    def __init__(self, atomics, edges):
        print("FlatGraph: edges -> routes")

        self.atomics = atomics
        self.routes = {}

        for src, dst in edges:
            self.routes.setdefault(src, []).append(dst)

        print(f"  routes = {self.routes}\n")


class Resultant(Atomic):
    def __init__(self, graph):
        print("Resultant: flat graph -> one atomic model\n")
        super().__init__("resultant")
        self.graph = graph

    def ta(self):
        next_time = min(atomic.ta() for atomic in self.graph.atomics.values())
        print(f"ta() -> {show_time(next_time)}")
        return next_time

    def lam(self):
        next_time = min(atomic.ta() for atomic in self.graph.atomics.values())
        outputs = [
            (name, atomic.lam())
            for name, atomic in self.graph.atomics.items()
            if atomic.ta() == next_time and atomic.lam() is not None
        ]
        print(f"lam() -> {outputs}")
        return outputs

    def delta_int(self):
        next_time = min(atomic.ta() for atomic in self.graph.atomics.values())
        assert next_time != INF, "deadlock"

        print(f"delta_int(): advance {show_time(next_time)}")

        for atomic in self.graph.atomics.values():
            atomic.sigma -= next_time

        imminent = [
            name for name, atomic in self.graph.atomics.items()
            if atomic.ta() == 0
        ]
        print(f"  imminent = {imminent}")

        bags = {}
        for src in imminent:
            output = self.graph.atomics[src].lam()
            if output is not None:
                for dst in self.graph.routes.get(src, []):
                    bags.setdefault(dst, []).append(output)
        print(f"  routed inputs = {bags}")

        for name, atomic in self.graph.atomics.items():
            if name in imminent:
                atomic.delta_int()
            if name in bags:
                atomic.delta_ext(bags[name])

        for name, atomic in self.graph.atomics.items():
            print(f"  {name}: phase={atomic.phase}, sigma={show_time(atomic.sigma)}")
        print()

    def delta_ext(self, bag):
        raise NotImplementedError("ping-pong takes no external input")
