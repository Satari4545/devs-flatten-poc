from pingpong import Atomic, Coupled, INF


def show_value(value):
    return "INF" if value == INF else repr(value)


# Step 1: remove coupled nesting and keep only leaf atomics + edges.
def dissolve(model):
    """Return leaf atomics and port-to-port edges from a coupled model."""
    atomics = {}
    edges = []

    def walk(node, prefix=""):
        children = {}
        for name, child in node.components.items():
            path = prefix + name
            if isinstance(child, Coupled):
                children[name] = walk(child, path + ".")
            else:
                atomics[path] = child
                children[name] = [path]

        for src, out_port, dst, in_port in node.ic:
            for src_path in children[src]:
                for dst_path in children[dst]:
                    edges.append((src_path, out_port, dst_path, in_port))

        return [path for paths in children.values() for path in paths]

    walk(model)
    return atomics, edges


# Compact LLM input: structure + semantics without generated code.
def flattened_summary(model):
    """Compact text summary for the LLM, cheaper than compiled source."""
    atomics, edges = dissolve(model)
    lines = ["Flattened DEVS summary", "", "Components:"]

    for name, atomic in sorted(atomics.items()):
        lines.append(
            f"- {name}: class={type(atomic).__name__}, "
            f"phase={atomic.phase}, sigma={show_value(atomic.sigma)}, "
            f"in={sorted(atomic.in_ports)}, out={sorted(atomic.out_ports)}"
        )

    lines += ["", "Couplings:"]
    for src, out_port, dst, in_port in edges:
        lines.append(f"- {src}.{out_port} -> {dst}.{in_port}")

    lines += [
        "",
        "Resultant semantics:",
        "- time_advance = min child sigma",
        "- output comes from children whose sigma equals the minimum",
        "- outputs are routed through the couplings into input bags",
        "- imminent children take internal_transition_function",
        "- children with input bags take external_transition_function",
        "- if both happen together, use confluent_transition_function",
    ]
    return "\n".join(lines)


# Routes are indexed by (source component, output port).
class FlatGraph:
    def __init__(self, atomics, edges):
        self.atomics = atomics
        self.routes = {}
        for src, out_port, dst, in_port in edges:
            self.routes.setdefault((src, out_port), []).append((dst, in_port))


# Step 2: this is the coupled model executed as one atomic resultant.
class Resultant(Atomic):
    """One atomic model that executes the flattened coupled model."""
    def __init__(self, graph):
        super().__init__("resultant")
        self.graph = graph
        self.elapsed = {name: 0.0 for name in graph.atomics}

    def time_advance(self):
        # Next event is whichever child atomic fires first.
        return min(a.time_advance() for a in self.graph.atomics.values())

    def output_function_lambda(self):
        dt = self.time_advance()
        return [
            (name, port, value)
            for name, atomic in self.graph.atomics.items()
            if atomic.time_advance() == dt
            for port, value in atomic.output_function_lambda()
        ]

    def internal_transition_function(self):
        dt = self.time_advance()
        assert dt != INF, "deadlock"

        atomics = self.graph.atomics
        outputs = self.output_function_lambda()

        for name, atomic in atomics.items():
            atomic.sigma -= dt
            self.elapsed[name] += dt

        # Route outputs into input bags, then apply DEVS transitions.
        bags = {}
        for src, port, value in outputs:
            for dst, in_port in self.graph.routes.get((src, port), []):
                bags.setdefault(dst, {}).setdefault(in_port, []).append(value)

        for name, atomic in atomics.items():
            imminent = atomic.time_advance() == 0
            bag = bags.get(name, {})
            if imminent and bag:
                atomic.confluent_transition_function(bag)
            elif imminent:
                atomic.internal_transition_function()
            elif bag:
                atomic.external_transition_function(self.elapsed[name], bag)
            if imminent or bag:
                self.elapsed[name] = 0.0

    def external_transition_function(self, elapsed_time, input_bag):
        raise NotImplementedError("closed model: no external input")

    def confluent_transition_function(self, input_bag):
        raise NotImplementedError("closed model: no external input")
