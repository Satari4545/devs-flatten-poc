"""Minimal resultant compiler for the ping-pong PoC.

It does four things, nothing more:
  1. CHECK: each player's code uses only simple constructs.
  2. COPY:  take their state variables and function bodies,
             renaming self.phase -> self.phase_A, and so on.
  3. PLUG:  drop the renamed pieces into the fixed event-procedure template.
  4. WRITE: save the standalone file.

Output: one atomic class with flat variables and five DEVS functions.
No player objects, no coordinator, no state enumeration.
"""

import ast
import copy
import inspect
import sys
import textwrap


# ---------------------------------------------------------------- 1. CHECK
# The bouncer: component code may only use these building blocks.
# Anything else (loops, imports, weird calls) is rejected with a clear error.

OK = (
    ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.If, ast.IfExp, ast.Expr, ast.Pass,
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Constant, ast.Attribute, ast.List, ast.Tuple, ast.Dict,
    ast.keyword, ast.Load, ast.Store,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.And, ast.Or, ast.Not, ast.USub, ast.In, ast.NotIn,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _call_ok(node, setup):
    f = node.func
    if setup:
        # __init__ may only call super().__init__() and add_in_port/out_port()
        if isinstance(f, ast.Name) and f.id == "super":
            return True
        return isinstance(f, ast.Attribute) and (
            (f.attr == "__init__" and isinstance(f.value, ast.Call))
            or (f.attr in ("add_in_port", "add_out_port")
                and isinstance(f.value, ast.Name)
                and f.value.id == "self"))
    # anywhere else: only input_bag.get(...)
    return (isinstance(f, ast.Attribute) and f.attr == "get"
            and isinstance(f.value, ast.Name)
            and f.value.id == "input_bag")


def check(tree, setup=False):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not _call_ok(node, setup):
                raise ValueError(f"unsupported call: {ast.unparse(node)}")
        elif not isinstance(node, OK):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")


# ---------------------------------------------------------------- 2. COPY
# Read a component class: its state variables, their starting values
# (taken from the live object), and its function bodies as code trees.

def _ast_of(cls, name):
    src = textwrap.dedent(inspect.getsource(cls.__dict__[name]))
    return ast.parse(src).body[0]


# Names the generated template uses. Component code may not assign to
# these as locals, or the inlined bodies would clash with the template.
_RESERVED = {"dt", "out", "bag", "src", "port", "val", "value",
             "t", "trace", "sender", "ball", "imminent"}


def _check_locals(bodies, tag):
    reserved = set(_RESERVED)
    reserved.update(f"imminent_{tag}", f"out_{tag}", f"bag_{tag}")
    for name, tree in bodies.items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in reserved:
                        raise ValueError(
                            f"{name}: local '{target.id}' clashes with a "
                            f"generated-template name; rename it")


def read_component(cls, obj, tag):
    bodies = {}
    for m in ("output_function_lambda", "internal_transition_function",
              "external_transition_function"):
        if m not in cls.__dict__:
            raise ValueError(f"{cls.__name__} must define {m}")
        tree = _ast_of(cls, m)
        check(tree)
        bodies[m] = tree
    # time_advance defaults to 'return self.sigma' when not overridden;
    # a custom one is rejected loudly in v0 (imminence assumes sigma residuals)
    if "time_advance" in cls.__dict__:
        raise ValueError(f"{cls.__name__}: custom time_advance not supported in v0")
    bodies["time_advance"] = None
    if "confluent_transition_function" in cls.__dict__:
        tree = _ast_of(cls, "confluent_transition_function")
        check(tree)
        bodies["confluent_transition_function"] = tree
    else:
        bodies["confluent_transition_function"] = None  # default: ext then int

    init_tree = _ast_of(cls, "__init__")
    check(init_tree, setup=True)
    var_names = []
    for node in ast.walk(init_tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                        and t.attr not in var_names):
                    var_names.append(t.attr)
    if "elapsed" in var_names:
        raise ValueError(f"{cls.__name__}: 'elapsed' is reserved for the compiler")
    _check_locals(bodies, tag)
    live = vars(obj)
    return {"tag": tag,
            "vars": var_names,
            "init": {v: live[v] for v in var_names},
            "bodies": bodies,
            "module": sys.modules[cls.__module__].__dict__}


class _Rename(ast.NodeTransformer):
    """self.x -> self.x_TAG, input_bag -> bag_TAG, elapsed_time -> self.elapsed_TAG."""

    def __init__(self, tag, bag):
        self.tag = tag
        self.bag = bag

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            node.attr = f"{node.attr}_{self.tag}"
        return node

    def visit_Name(self, node):
        if node.id == "input_bag":
            return ast.copy_location(ast.Name(id=self.bag, ctx=node.ctx), node)
        if node.id == "elapsed_time":
            return ast.copy_location(ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr=f"elapsed_{self.tag}", ctx=node.ctx), node)
        return node


class _InlineReturns(ast.NodeTransformer):
    """return E  ->  out.extend((TAG, port, value) for port, value in E)."""

    def __init__(self, tag, out):
        self.tag = tag
        self.out = out

    def visit_Return(self, node):
        e = node.value
        if e is None or (isinstance(e, ast.List) and not e.elts):
            return None  # 'return []' adds nothing; drop it
        gen = ast.GeneratorExp(
            elt=ast.Tuple(elts=[ast.Constant(value=self.tag),
                                ast.Name(id="port", ctx=ast.Load()),
                                ast.Name(id="value", ctx=ast.Load())],
                          ctx=ast.Load()),
            generators=[ast.comprehension(
                target=ast.Tuple(elts=[ast.Name(id="port", ctx=ast.Store()),
                                        ast.Name(id="value", ctx=ast.Store())],
                                 ctx=ast.Store()),
                iter=e, ifs=[], is_async=0)])
        call = ast.Call(
            func=ast.Attribute(value=ast.Name(id=self.out, ctx=ast.Load()),
                               attr="extend", ctx=ast.Load()),
            args=[gen], keywords=[])
        return ast.copy_location(ast.Expr(value=call), node)


def _inline_body(fn_ast, tag, bag, out=None):
    """Renamed body statements as source lines; optionally rewrite returns."""
    tree = _Rename(tag, bag).visit(copy.deepcopy(fn_ast))
    if out is not None:
        tree = _InlineReturns(tag, out).visit(tree)
    tree = ast.fix_missing_locations(tree)
    lines = [ast.unparse(s) for s in tree.body] or ["pass"]
    return lines


def _collect_constants(comps):
    """Module-level names used by bodies (HIT_TIME, INF, ...)."""
    bound = {"self", "input_bag", "elapsed_time", "True", "False", "None"}
    names = set()
    for c in comps:
        for tree in c["bodies"].values():
            if tree is None:
                continue
            bound.update(a.arg for a in tree.args.args)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    names.add(node.id)
    found = {}
    for n in sorted(names - bound):
        for c in comps:
            if n in c["module"]:
                found[n] = c["module"][n]
    return found


def _format_value(v):
    if v == float("inf"):
        return 'float("inf")'
    if v == float("-inf"):
        return 'float("-inf")'
    return repr(v)


# ---------------------------------------------------------------- 3. PLUG
# Fill the fixed event-procedure template with the renamed pieces.
# Component count is known, so per-component blocks are written out
# directly. The imminent set is found at runtime (a scheduler scan,
# not a loop over component objects).

def _indent(lines, n):
    return textwrap.indent("\n".join(lines), " " * n)


def build_source(comps, edges, classname):
    tags = [c["tag"] for c in comps]

    init_lines = []
    for c in comps:
        init_lines.append(f"# was component {c['tag']}")
        for v in c["vars"]:
            init_lines.append(
                f"self.{v}_{c['tag']} = {_format_value(c['init'][v])}")
        init_lines.append(f"self.elapsed_{c['tag']} = 0.0")

    ta_expr = "min(" + ", ".join(f"self.sigma_{t}" for t in tags) + ")"

    lam_lines = ["dt = " + ta_expr, "out = []"]
    for c in comps:
        t = c["tag"]
        lam_lines.append(f"if self.sigma_{t} == dt:")
        lam_lines.append(_indent(_inline_body(
            c["bodies"]["output_function_lambda"], t, f"bag_{t}", out="out"), 4))
    lam_lines.append("return out")

    dint_lines = [f"dt = {ta_expr}", 'assert dt != INF, "deadlock"']
    for t in tags:
        dint_lines.append(f"imminent_{t} = (self.sigma_{t} == dt)")
    dint_lines.append("# outputs from imminent components, before they change")
    for c in comps:
        t = c["tag"]
        dint_lines.append(f"out_{t} = []")
        dint_lines.append(f"if imminent_{t}:")
        dint_lines.append(_indent(_inline_body(
            c["bodies"]["output_function_lambda"], t, f"bag_{t}",
            out=f"out_{t}"), 4))
    dint_lines.append("# static routing through the couplings")
    for t in tags:
        dint_lines.append(f"bag_{t} = {{}}")
    for s, sp, dst, dp in edges:
        dint_lines.append(f"for src, port, val in out_{s}:")
        dint_lines.append(f"    if port == {sp!r}:")
        dint_lines.append(f"        bag_{dst}.setdefault({dp!r}, []).append(val)")
    dint_lines.append("# advance clocks")
    for t in tags:
        dint_lines.append(f"self.sigma_{t} -= dt")
        dint_lines.append(f"self.elapsed_{t} += dt")
    dint_lines.append("# apply transitions (elapsed is read before it is reset)")
    for c in comps:
        t = c["tag"]
        dext = _inline_body(
            c["bodies"]["external_transition_function"], t, f"bag_{t}")
        dint = _inline_body(
            c["bodies"]["internal_transition_function"], t, f"bag_{t}")
        dconf = (_inline_body(
            c["bodies"]["confluent_transition_function"], t, f"bag_{t}")
            if c["bodies"]["confluent_transition_function"] is not None
            else dext + dint)  # default: external then internal
        dint_lines.append(f"if imminent_{t} and bag_{t}:")
        dint_lines.append(_indent(dconf + [f"self.elapsed_{t} = 0.0"], 4))
        dint_lines.append(f"elif imminent_{t}:")
        dint_lines.append(_indent(dint + [f"self.elapsed_{t} = 0.0"], 4))
        dint_lines.append(f"elif bag_{t}:")
        dint_lines.append(_indent(dext + [f"self.elapsed_{t} = 0.0"], 4))

    src = []
    src.append('"""Standalone atomic generated by compiler.py. Do not hand-edit;')
    src.append('regenerate instead. Flat state, five DEVS functions, no component')
    src.append('objects, no coordinator, no state enumeration."""')
    for k, v in _collect_constants(comps).items():
        src.append(f"{k} = {_format_value(v)}")
    src.append("")
    src.append(f"class {classname}:")
    src.append("    def __init__(self):")
    src.append(_indent(init_lines, 8))
    src.append("")
    src.append("    def time_advance(self):")
    src.append(f"        return {ta_expr}")
    src.append("")
    src.append("    def output_function_lambda(self):")
    src.append(_indent(lam_lines, 8))
    src.append("")
    src.append("    def internal_transition_function(self):")
    src.append(_indent(dint_lines, 8))
    src.append("")
    src.append("    def external_transition_function(self, elapsed_time, input_bag):")
    src.append('        raise NotImplementedError("closed model: no external input")')
    src.append("")
    src.append("    def confluent_transition_function(self, input_bag):")
    src.append('        raise NotImplementedError("closed model: no external input")')
    src.append("")
    src.append("    def run(self, hits=10):")
    src.append("        t, trace = 0.0, []")
    src.append("        for _ in range(hits):")
    src.append("            t += self.time_advance()")
    src.append("            trace.extend((round(t, 10), sender, value)")
    src.append("                         for sender, port, value in self.output_function_lambda())")
    src.append("            self.internal_transition_function()")
    src.append("        return trace")
    src.append("")
    src.append('if __name__ == "__main__":')
    src.append(f"    for t, sender, ball in {classname}().run():")
    src.append('        print(f"t={t:g}  {sender} !send {ball}")')
    return "\n".join(src) + "\n"
    return "\n".join(src) + "\n"


# ---------------------------------------------------------------- 4. WRITE
# Generate the file, check its shape, and prove it behaves identically.

def compile_model(coupled, out_path, classname):
    from flatten import dissolve
    atomics, edges = dissolve(coupled)
    comps = [read_component(type(a), a, n) for n, a in sorted(atomics.items())]
    src = build_source(comps, edges, classname)
    for banned in ("Player", "FlatGraph", "Resultant", "for atomic in"):
        assert banned not in src, f"leak: {banned}"
    for fn in ("def time_advance", "def output_function_lambda",
               "def internal_transition_function",
               "def external_transition_function",
               "def confluent_transition_function"):
        assert fn in src, f"missing: {fn}"
    with open(out_path, "w") as f:
        f.write(src)
    print(f"compiler: wrote {out_path}")
    return out_path


EXPECTED = [(round(0.1 * t, 10), "A" if t % 2 == 1 else "B", "ball")
            for t in range(1, 11)]


if __name__ == "__main__":
    import importlib.util

    from pingpong import PingPong

    path = compile_model(PingPong(), "compiled_pingpong.py", "CompiledPingPong")
    spec = importlib.util.spec_from_file_location("cp", path)
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    got = cp.CompiledPingPong().run()
    assert got == EXPECTED, got
    print("compiler: generated trace matches OK")
