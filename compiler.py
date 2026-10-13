"""Resultant compiler, M1a: restricted-Python components -> standalone atomic.

Reads a single-level closed coupled model, validates each component
against the DEVS-IR v0 subset, builds the symbolic product IR, and emits
one standalone atomic class: flat state vector, five DEVS functions,
no component objects, no coordinator, no runtime table lookups,
no state-space enumeration.

Scope (v0): single-level closed coupled models. No EIC/EOC (M1c).
Fusion (rung 3) is a later, separate step -- this stops at rung 2.
"""

import ast
import copy
import inspect
import sys
import textwrap

# ------------------------------------------------------------------ frontend
# DEVS-IR v0: assignments, if/else, comparisons, arithmetic, boolean ops,
# bag.get(...) calls only. Everything else is rejected with a clear error.

_CMP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
_SIMPLE = (
    ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.If, ast.IfExp, ast.Expr, ast.Pass,
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Constant, ast.Attribute, ast.List, ast.Tuple, ast.Dict,
    ast.keyword, ast.Load, ast.Store,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.And, ast.Or, ast.Not, ast.USub,
) + _CMP


def _call_ok(node, in_init):
    f = node.func
    if in_init:
        # super().__init__(...) or self.add_in_port/add_out_port(...)
        if isinstance(f, ast.Name) and f.id == "super":
            return True
        if (isinstance(f, ast.Attribute) and f.attr == "__init__"
                and isinstance(f.value, ast.Call)
                and isinstance(f.value.func, ast.Name)
                and f.value.func.id == "super"):
            return True
        return (isinstance(f, ast.Attribute)
                and f.attr in ("add_in_port", "add_out_port")
                and isinstance(f.value, ast.Name)
                and f.value.id == "self")
    # input_bag.get(...) only
    return (isinstance(f, ast.Attribute) and f.attr == "get"
            and isinstance(f.value, ast.Name)
            and f.value.id == "input_bag")


def validate_method(fn_ast, in_init=False):
    for node in ast.walk(fn_ast):
        if isinstance(node, ast.Call):
            if not _call_ok(node, in_init):
                raise ValueError(f"unsupported call: {ast.unparse(node)}")
        elif not isinstance(node, _SIMPLE):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")


def _parse_method(cls, name):
    src = inspect.getsource(cls.__dict__[name])
    return ast.parse(textwrap.dedent(src)).body[0]


def extract_component(cls, instance, cname):
    """Validate + extract one component. Values come from the live instance;
    names and code come from validated ASTs."""
    fns = {}
    for m in ("time_advance", "output_function_lambda",
              "internal_transition_function",
              "external_transition_function"):
        if m in cls.__dict__:
            fn_ast = _parse_method(cls, m)
            validate_method(fn_ast)
            fns[m] = fn_ast
        elif m.startswith(("output", "internal", "external")):
            raise ValueError(f"{cls.__name__} must define {m}")
        else:
            fns[m] = None  # default: return self.sigma
    dconf_defined = "confluent_transition_function" in cls.__dict__

    init_ast = _parse_method(cls, "__init__")
    validate_method(init_ast, in_init=True)
    state_vars = []
    for node in ast.walk(init_ast):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                        and t.attr not in state_vars):
                    state_vars.append(t.attr)
    live = vars(instance)
    init = {v: live[v] for v in state_vars}
    return {"name": cname, "cls": cls.__name__, "vars": state_vars,
            "init": init, "fns": fns, "dconf_defined": dconf_defined,
            "module": sys.modules[cls.__module__].__dict__}


def collect_constants(comp):
    """Module-level names referenced by method bodies (e.g. HIT_TIME, INF)."""
    bound = {"self", "input_bag", "elapsed_time", "True", "False", "None"}
    for fn_ast in comp["fns"].values():
        if fn_ast is not None:
            bound.update(a.arg for a in fn_ast.args.args)
    names = set()
    for fn_ast in comp["fns"].values():
        if fn_ast is None:
            continue
        for node in ast.walk(fn_ast):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                names.add(node.id)
    consts = {}
    for n in sorted(names - bound):
        if n in comp["module"]:
            consts[n] = comp["module"][n]
    return consts


def const_def(name, value):
    if value == float("inf"):
        return f'{name} = float("inf")'
    if value == float("-inf"):
        return f'{name} = float("-inf")'
    return f"{name} = {value!r}"


def fmt_val(value):
    # value literal for generated source (no name attached)
    if value == float("inf"):
        return 'float("inf")'
    if value == float("-inf"):
        return 'float("-inf")'
    return repr(value)

# ------------------------------------------------------------------ IR build
# ProductIR: symbolic flat state + static routes + component ASTs.
# Nothing here enumerates states; n is known, so codegen unrolls it.

def build_ir(coupled, atomics, edges):
    comps = [extract_component(type(a), a, n) for n, a in sorted(atomics.items())]
    return {"components": comps, "ic": edges}


# ------------------------------------------------------------------ codegen

class _Renamer(ast.NodeTransformer):
    """self.X -> self.X_<comp>; input_bag -> bagvar; elapsed_time -> self.elapsed_<comp>."""

    def __init__(self, comp, bagvar):
        self.comp = comp
        self.bagvar = bagvar

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            node.attr = f"{node.attr}_{self.comp}"
        return node

    def visit_Name(self, node):
        if node.id == "input_bag":
            return ast.copy_location(ast.Name(id=self.bagvar, ctx=node.ctx), node)
        if node.id == "elapsed_time":
            return ast.copy_location(ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr=f"elapsed_{self.comp}", ctx=node.ctx), node)
        return node


def _rename(fn_ast, comp, bagvar):
    tree = _Renamer(comp, bagvar).visit(copy.deepcopy(fn_ast))
    return ast.fix_missing_locations(tree)


class _ReturnToOut(ast.NodeTransformer):
    """return E  ->  __out.extend((comp, p, v) for p, v in E)."""

    def __init__(self, comp, target):
        self.comp = comp
        self.target = target

    def visit_Return(self, node):
        e = node.value
        if e is None or (isinstance(e, ast.List) and not e.elts):
            return None  # return [] adds nothing; drop it
        gen = ast.GeneratorExp(
            elt=ast.Tuple(elts=[ast.Constant(value=self.comp),
                                ast.Name(id="__p", ctx=ast.Load()),
                                ast.Name(id="__v", ctx=ast.Load())],
                          ctx=ast.Load()),
            generators=[ast.comprehension(
                target=ast.Tuple(elts=[ast.Name(id="__p", ctx=ast.Store()),
                                        ast.Name(id="__v", ctx=ast.Store())],
                                 ctx=ast.Store()),
                iter=e, ifs=[], is_async=0)])
        call = ast.Call(
            func=ast.Attribute(value=ast.Name(id=self.target, ctx=ast.Load()),
                               attr="extend", ctx=ast.Load()),
            args=[gen], keywords=[])
        return ast.copy_location(ast.Expr(value=call), node)


def _stmts(fn_ast, comp, bagvar, out_target=None):
    """Renamed body statements, ready to unparse. Optionally rewrite returns."""
    tree = _rename(fn_ast, comp, bagvar)
    if out_target is not None:
        tree = _ReturnToOut(comp, out_target).visit(tree)
        tree = ast.fix_missing_locations(tree)
    if not tree.body:
        tree.body = [ast.Pass()]
    return [ast.unparse(s) for s in tree.body]


def _ind(lines, n):
    return textwrap.indent("\n".join(lines), " " * n)


def generate(ir, classname):
    comps = ir["components"]
    cnames = [c["name"] for c in comps]

    # module constants referenced by bodies
    consts = {}
    for c in comps:
        consts.update(collect_constants(c))

    # ---- __init__: flat state vector (values from live instances)
    init_lines = []
    for c in comps:
        init_lines.append(f"# component {c['name']}")
        for v in c["vars"]:
            init_lines.append(f"self.{v}_{c['name']} = {fmt_val(c['init'][v])}")
        init_lines.append(f"self.elapsed_{c['name']} = 0.0")

    # ---- time_advance: min over component residuals (unrolled)
    ta_expr = "min(" + ", ".join(f"self.sigma_{n}" for n in cnames) + ")"

    # ---- output_function_lambda: guarded inlined lams -> (comp, port, value)
    lam_blocks = []
    for c in comps:
        n = c["name"]
        body = _stmts(c["fns"]["output_function_lambda"], n, f"__bag_{n}",
                      out_target="__out")
        lam_blocks.append(f"if self.sigma_{n} == __dt:")
        lam_blocks.append(_ind(body, 4))

    # ---- internal_transition_function: the canonical event procedure
    dint = []
    dint.append(f"__dt = {ta_expr}")
    dint.append('assert __dt != INF, "deadlock"')
    for n in cnames:
        dint.append(f"__imm_{n} = (self.sigma_{n} == __dt)")
    dint.append("# 1. outputs from imminent components (pre-transition states)")
    for c in comps:
        n = c["name"]
        dint.append(f"__out_{n} = []")
        dint.append(f"if __imm_{n}:")
        dint.append(_ind(_stmts(c["fns"]["output_function_lambda"], n,
                                f"__bag_{n}", out_target=f"__out_{n}"), 4))
    dint.append("# 2. static routing through internal couplings")
    for n in cnames:
        dint.append(f"__bag_{n} = {{}}")
    for s, sp, d, dp in ir["ic"]:
        dint.append(f"for __cc, __pp, __vv in __out_{s}:")
        dint.append(f"    if __pp == {sp!r}:")
        dint.append(f"        __bag_{d}.setdefault({dp!r}, []).append(__vv)")
    dint.append("# 3. advance clocks")
    for n in cnames:
        dint.append(f"self.sigma_{n} -= __dt")
        dint.append(f"self.elapsed_{n} += __dt")
    dint.append("# 4. dispatch (elapsed read before reset)")
    for c in comps:
        n = c["name"]
        dext = _stmts(c["fns"]["external_transition_function"], n, f"__bag_{n}")
        dintb = _stmts(c["fns"]["internal_transition_function"], n, f"__bag_{n}")
        if c["dconf_defined"]:
            dconf = _stmts(c["fns"]["confluent_transition_function"], n,
                           f"__bag_{n}")
        else:
            dconf = dext + dintb  # default: external then internal
        dint.append(f"if __imm_{n} and __bag_{n}:")
        dint.append(_ind(dconf + [f"self.elapsed_{n} = 0.0"], 4))
        dint.append(f"elif __imm_{n}:")
        dint.append(_ind(dintb + [f"self.elapsed_{n} = 0.0"], 4))
        dint.append(f"elif __bag_{n}:")
        dint.append(_ind(dext + [f"self.elapsed_{n} = 0.0"], 4))

    out = []
    out.append('"""Standalone atomic generated by compiler.py (M1a).')
    out.append('')
    out.append('Flat product state, five DEVS functions, no component objects,')
    out.append('no coordinator, no runtime table lookups, no state enumeration.')
    out.append('Do not hand-edit; regenerate instead.')
    out.append('"""')
    for k, v in consts.items():
        out.append(const_def(k, v))
    out.append("")
    out.append(f"class {classname}:")
    out.append("    def __init__(self):")
    out.append(_ind(init_lines, 8))
    out.append("")
    out.append("    def time_advance(self):")
    out.append(f"        return {ta_expr}")
    out.append("")
    out.append("    def output_function_lambda(self):")
    out.append("        __dt = " + ta_expr)
    out.append("        __out = []")
    out.append(_ind(lam_blocks, 8))
    out.append("        return __out")
    out.append("")
    out.append("    def internal_transition_function(self):")
    out.append(_ind(dint, 8))
    out.append("")
    out.append("    def external_transition_function(self, elapsed_time, input_bag):")
    out.append('        raise NotImplementedError("closed model: no external input")')
    out.append("")
    out.append("    def confluent_transition_function(self, input_bag):")
    out.append('        raise NotImplementedError("closed model: no external input")')
    out.append("")
    out.append("    def run(self, hits=10):")
    out.append("        __t, __trace = 0.0, []")
    out.append("        for _ in range(hits):")
    out.append("            __t += self.time_advance()")
    out.append("            __trace.extend((round(__t, 10), __s, __v)")
    out.append("                           for __s, __p, __v in self.output_function_lambda())")
    out.append("            self.internal_transition_function()")
    out.append("        return __trace")
    out.append("")
    out.append('if __name__ == "__main__":')
    out.append(f"    for __t, __s, __o in {classname}().run():")
    out.append('        print(f"t={__t:g}  {__s} !send {__o}")')
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ driver

def compile_model(coupled, out_path, classname):
    from flatten import dissolve
    atomics, edges = dissolve(coupled)
    ir = build_ir(coupled, atomics, edges)
    src = generate(ir, classname)
    # structural smoke checks: no component machinery left in the output
    for banned in ("Player", "FlatGraph", "Resultant", "for atomic in",
                   "confluent_transition_function(bag)"):
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
