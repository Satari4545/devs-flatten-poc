"""Load, check and flatten DEVSMap models (atomic rules: devsmap/SKILL.md).

This module is the one reader and validator of the profile: the subset
of DEVSMap that the vendored DEVSMap_to_Cadmium_Generator translates
correctly. Everything outside it is a ValueError naming the file or the
model, the function and the expression. Stdlib only.

    load_model(path)         -> model (below); reads every file it needs
    model_name(doc, where)   -> the one model key of a parsed document
    write_json(path, doc)    -> write a document in the profile's layout
    flatten(model)           -> leaf atomics + leaf-to-leaf couplings
    flattened_summary(model) -> compact text for the LLM

A model is a dict:

    name        model name
    kind        "atomic" or "coupled"
    path        absolute path of <name>_<kind>.json
    body        the model body exactly as written in the file
    components  (coupled) [{"model", "id"}], the paper's components map
                ({model name: [id, ...]}) as a list in file order
    models      (coupled) {component model name: model}, loaded from the
                sibling <model name>_atomic.json / _coupled.json
    init        (load_model's result only) the initial state of every
                atomic instance, {instance path: {state variable: value}},
                from the one <name>_init_state.json next to the top model
                file. Paths are component ids ("A", "sub.leaf"); a
                standalone atomic's single entry is keyed by its name.
                Every instance sets exactly its own state variables.

Expressions are type-checked by a small tokenizer and recursive-descent
parser (_Parser) against what the generator rewrites: it prefixes state
variables by plain text replacement of "<var> " and "<var>;", turns the
exact bag forms below into Cadmium calls, and copies everything else
verbatim into C++. So the checks are textual where the generator is.

Usage: python devsmap.py <name>_atomic.json|<name>_coupled.json
"""

import json
import os
import re
import sys

SETS = ("string", "R")
TRANSITIONS = ("delta_int", "delta_ext", "delta_con")
ATOMIC_KEYS = ("x", "y", "s", *TRANSITIONS, "lambda", "ta")
COUPLED_KEYS = ("x", "y", "components", "eic", "eoc", "ic")
COUPLING_KEYS = {
    "ic": ("component_from", "port_from", "component_to", "port_to"),
    "eic": ("port_from", "component_to", "port_to"),
    "eoc": ("component_from", "port_from", "port_to"),
}
CPP_KEYWORDS = frozenset("""
    alignas alignof and and_eq asm auto bitand bitor bool break case catch
    char char8_t char16_t char32_t class compl concept const consteval
    constexpr constinit const_cast continue co_await co_return co_yield
    decltype default delete do double dynamic_cast else enum explicit
    export extern false float for friend goto if inline int long mutable
    namespace new noexcept not not_eq nullptr operator or or_eq private
    protected public register reinterpret_cast requires return short
    signed sizeof static static_assert static_cast struct switch template
    this thread_local throw true try typedef typeid typename union
    unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())
# Names the generated C++ (or Cadmium, the sim driver, the C library)
# already uses; a model, port, state variable or component id named like
# one of them does not compile or means something else.
RESERVED = CPP_KEYWORDS | frozenset("""
    e inf state id initial_state std cadmium
    Port Atomic Coupled Component output internalTransition
    externalTransition confluentTransition timeAdvance logState
    addComponent addCoupling addInPort addOutPort
    main GenModel TraceLogger TooManyEvents kTooManyEvents wrapper top
    errno stdin stdout stderr
""".split())
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
STRING_LITERAL = re.compile(r'"[A-Za-z0-9_.:-]*"')
REAL_CONSTANT = re.compile(r"-?[0-9]+\.[0-9]+|inf")
# Bag operators, exactly as the generator's rewrite patterns spell them.
BAG_FORMS = "'p.bagSize() != 0', 'p.bagSize() == 0', 'p.bag(-1)'"


def _unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _read_json(path):
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, object_pairs_hook=_unique_keys)
    except OSError as err:
        raise ValueError(f"{name}: cannot read: {err.strerror}") from None
    except (ValueError, RecursionError) as err:
        raise ValueError(f"{name}: invalid JSON: {err}") from None


def write_json(path, doc):
    """Write `doc` to `path` as the profile's files are: 2-space indent."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def _check_name(where, kind, name):
    if not (isinstance(name, str) and IDENT.fullmatch(name)
            and "__" not in name):
        raise ValueError(f"{where}: {kind} {name!r} is not an identifier "
                         f"(letters, digits, _, starting with a letter, "
                         f"no __)")
    # <atomic>State is the generator's state struct; all-uppercase names
    # are macro territory (EOF, NULL).
    if name in RESERVED or name.endswith("State") \
            or len(name) > 1 and name.isupper():
        raise ValueError(f"{where}: {kind} {name!r} is reserved (see "
                         f"Names in devsmap/SKILL.md)")


def _check_sets(where, names, kind):
    """x, y or s: {name: set}."""
    if not isinstance(names, dict):
        raise ValueError(f"{where}: expected {{{kind}: set}}, got {names!r}")
    for name, typ in names.items():
        _check_name(where, kind, name)
        if typ not in SETS:
            raise ValueError(f"{where}: {kind} '{name}' has set {typ!r} "
                             f"(only {list(SETS)})")


# --- expressions ------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<space>\ +)
  | (?P<bag_size>[A-Za-z_][A-Za-z0-9_]*\.bagSize\(\)\ [!=]=\ 0)
  | (?P<bag>[A-Za-z_][A-Za-z0-9_]*\.bag\(-1\))
  | (?P<number>[0-9]+(?:\.[0-9]+)?)
  | (?P<string>"[^"]*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||[-+*/()<>!])
""", re.X)


def _tokenize(text):
    """[(kind, text, end offset)], spaces dropped."""
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            hint = f" (bag operators: {BAG_FORMS})" if text[pos] == "." else ""
            raise ValueError(f"unsupported text {text[pos:]!r}{hint}")
        pos = m.end()
        if m.group() == "-" and text.startswith("-", pos):
            raise ValueError("'--' is C++'s decrement operator; write '- -'")
        if m.lastgroup != "space":
            tokens.append((m.lastgroup, m.group(), pos))
    return tokens


def _guarded_ports(cond):
    """Ports p whose `p.bagSize() != 0` is a top-level && term of `cond`:
    a branch with that condition only runs when p holds a value."""
    tokens, depth, ports = _tokenize(cond), 0, set()
    for i, (kind, text, _) in enumerate(tokens):
        depth += (text == "(") - (text == ")")
        if depth == 0 and text == "||":
            return set()
        if depth == 0 and kind == "bag_size" and text.endswith("!= 0") \
                and (i == 0 or tokens[i - 1][1] != "!"):
            ports.add(text.split(".")[0])
    return ports


def _set_of(typ):
    return "string" if typ == "literal" else typ


class _Parser:
    """Type of one expression: "R", "bool", "string" or "literal" (a string
    literal). C++ precedence: unary - !, then * /, + -, one comparison,
    &&, ||. `states` maps state variables to their set; `inputs` is the
    model's x in delta_ext (bag operators allowed, and `e`), else None."""

    def __init__(self, text, states, inputs, assignment):
        self.text, self.states, self.inputs = text, states, inputs
        self.assignment = assignment
        self.tokens, self.pos = _tokenize(text), 0
        self.state_uses = []  # (name, end offset) of each state variable

    def parse(self):
        typ = self.disjunction()
        if self.pos < len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.pos][1]!r}")
        # The generator only prefixes "<var> " and "<var>;" ("state.<var>"),
        # and appends ";" after an assignment's expression.
        for name, end in self.state_uses:
            if not (self.text[end:end + 1] == " "
                    or end == len(self.text) and self.assignment):
                raise ValueError(f"state variable '{name}' must be followed "
                                 f"by a space")
        return typ

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos][1]
        return None

    def next(self):
        if self.pos == len(self.tokens):
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return self.tokens[self.pos - 1]

    def chain(self, ops, operand, typ):
        """operand (op operand)*, every operand of type `typ`."""
        left = operand()
        while self.peek() in ops:
            op = self.next()[1]
            right = operand()
            if left != typ or right != typ:
                raise ValueError(f"'{op}' needs {typ} operands")
            left = typ
        return left

    def disjunction(self):
        return self.chain(("||",), self.conjunction, "bool")

    def conjunction(self):
        return self.chain(("&&",), self.comparison, "bool")

    def comparison(self):
        left = self.sum()
        if self.peek() not in ("==", "!=", "<", "<=", ">", ">="):
            return left
        op = self.next()[1]
        right = self.sum()
        sets = {_set_of(left), _set_of(right)}
        if sets == {"string"} and op in ("==", "!=") \
                and (left, right) != ("literal", "literal"):
            return "bool"
        if sets != {"R"}:
            raise ValueError(
                f"'{op}' cannot compare {left} with {right} (R with R; "
                f"strings only with == or !=, and not two literals)")
        return "bool"

    def sum(self):
        return self.chain(("+", "-"), self.product, "R")

    def product(self):
        return self.chain(("*", "/"), self.unary, "R")

    def unary(self):
        for op, typ in (("-", "R"), ("!", "bool")):
            if self.peek() == op:
                self.next()
                if self.unary() != typ:
                    raise ValueError(f"'{op}' needs a {typ} operand")
                return typ
        return self.primary()

    def primary(self):
        kind, text, end = self.next()
        if kind == "number":
            if "." not in text:
                raise ValueError(f"write {text} as {text}.0 (an integer "
                                 f"literal does integer arithmetic in C++)")
            return "R"
        if kind == "string":
            if not STRING_LITERAL.fullmatch(text):
                raise ValueError(f"string literal {text} may only contain "
                                 f"letters, digits and _ . : -")
            return "literal"
        if text == "(":
            typ = self.disjunction()
            if self.next()[1] != ")":
                raise ValueError("expected ')'")
            return typ
        if kind == "name":
            return self.name(text, end)
        if kind in ("bag_size", "bag"):
            return self.bag(kind, text)
        raise ValueError(f"unexpected {text!r}")

    def name(self, text, end):
        if text in self.states:
            self.state_uses.append((text, end))
            return self.states[text]
        if text == "e" and self.inputs is not None:
            return "R"
        if text == "e":
            raise ValueError("e (elapsed time) may only be used in delta_ext")
        if text == "inf":
            raise ValueError("inf may only be a whole value: an initial "
                             "value or a delta_int assignment's entire "
                             "right-hand side")
        if self.inputs and text in self.inputs:
            raise ValueError(f"input port '{text}' may only be used through "
                             f"a bag operator: {BAG_FORMS}")
        raise ValueError(f"unknown name '{text}'")

    def bag(self, kind, text):
        port = text.split(".")[0]
        if self.inputs is None:
            raise ValueError("bag operators may only be used in delta_ext")
        if port not in self.inputs:
            raise ValueError(f"'{port}' is not an input port")
        if kind == "bag_size":
            if self.assignment:
                raise ValueError(f"{text} may only be used in a condition")
            return "bool"
        if not self.assignment:
            raise ValueError(f"{text} may only be used in an assignment")
        return self.inputs[port]


def _check_expr(where, expr, want, states, inputs=None, assignment=False):
    """`expr` must be a profile expression of set `want` ("bool" for a
    condition)."""
    if not isinstance(expr, str):
        raise ValueError(f"{where}: expression must be a string, "
                         f"got {expr!r}")
    try:
        typ = _Parser(expr, states, inputs, assignment).parse()
    except ValueError as err:
        raise ValueError(f"{where}: {expr!r}: {err}") from None
    except RecursionError:
        raise ValueError(f"{where}: expression nested too deeply") from None
    if _set_of(typ) != want:
        raise ValueError(f"{where}: {expr!r} is {_set_of(typ)}, "
                         f"expected {want}")


def _check_value(where, value, typ):
    """An initial value: a constant of the state variable's set."""
    if typ == "R":
        ok, expected = REAL_CONSTANT, 'a real number like "0.1", or "inf"'
    else:
        ok, expected = STRING_LITERAL, ('a string literal like "\\"Send\\"" '
                                        '(letters, digits and _ . : -)')
    if not (isinstance(value, str) and ok.fullmatch(value)):
        raise ValueError(f"{where}: initial value {value!r} must be "
                         f"{expected}")


# --- models -----------------------------------------------------------------

def _check_keys(where, body, keys, kind):
    for key in keys:
        if key not in body:
            raise ValueError(f"{where}: missing key \"{key}\"")
    extra = sorted(set(body) - set(keys))
    if extra:
        raise ValueError(f"{where}: unknown keys {extra} (a {kind} has "
                         f"exactly {list(keys)})")


def _check_atomic(name, body):
    where = f"atomic '{name}'"
    # Initial states live only in <name>_init_state.json files.
    if "initial" in body:
        raise ValueError(f"{where}: \"initial\" is not allowed in an "
                         f"atomic model; put initial states in a "
                         f"<name>_init_state.json file")
    _check_keys(where, body, ATOMIC_KEYS, "atomic")
    seen = {name: "the model name"}
    for key, kind in (("x", "input port"), ("y", "output port"),
                      ("s", "state variable")):
        _check_sets(where, body[key], kind)
        for n in body[key]:
            if n in seen:
                raise ValueError(f"{where}: {kind} '{n}' is also "
                                 f"{seen[n]}; names must be distinct")
            seen[n] = ("an " if kind[0] in "aeiou" else "a ") + kind
    states = body["s"]

    # The generator rewrites "<var> " to "state.<var> " by plain text
    # replacement, so a state variable must not end any other name that is
    # written before a space: state variables, lambda targets, if, else,
    # and inf (written as " inf;").
    for var in states:
        for other in (*states, *body["y"], "if", "else", "inf"):
            if other != var and other.endswith(var):
                raise ValueError(f"{where}: state variable '{var}' is the "
                                 f"end of '{other}'; the generator would "
                                 f"rewrite '{other}'")
    # lambda lines starting with "if"/"else" are taken for if/else lines.
    for port in body["y"]:
        if port.startswith(("if", "else")):
            raise ValueError(f"{where}: output port '{port}' must not "
                             f"start with 'if' or 'else'")

    def function(key, targets, kind, inputs=None):
        cmap = body[key]
        if not isinstance(cmap, dict) or list(cmap)[-1:] != ["otherwise"]:
            raise ValueError(f"{where} {key}: expected a condition map "
                             f"ending in \"otherwise\"")
        for cond, branch in cmap.items():
            w = f"{where} {key} [{cond}]"
            if cond != "otherwise":
                _check_expr(w, cond, "bool", states, inputs)
            if not isinstance(branch, dict):
                raise ValueError(f"{w}: expected {{{kind}: expression}}, "
                                 f"got {branch!r}")
            # p.bag(-1) of an empty bag is undefined behaviour in C++, so it
            # is read only where p surely holds a value ("otherwise" has no
            # guard terms).
            filled = set(inputs) if inputs and len(inputs) == 1 \
                else _guarded_ports(cond)
            for target, expr in branch.items():
                if target not in targets:
                    raise ValueError(f"{w}: unknown {kind} {target!r}")
                if key == "delta_int" and expr == "inf" \
                        and targets[target] == "R":
                    continue
                _check_expr(f"{w} '{target}'", expr, targets[target],
                            states, inputs, assignment=True)
                for token, text, _ in _tokenize(expr):
                    port = text.split(".")[0]
                    if token == "bag" and port not in filled:
                        raise ValueError(
                            f"{w} '{target}': {text} may be empty here; read "
                            f"it only in a branch whose condition has "
                            f"'{port}.bagSize() != 0' as a top-level && term "
                            f"(or when '{port}' is the only input port)")

    function("delta_int", states, "state variable")
    function("delta_ext", states, "state variable", inputs=body["x"])
    function("lambda", body["y"], "output port")
    # The generator emits no confluent transition (Cadmium's default
    # applies: internal, then external) and only ta = one state variable.
    if body["delta_con"] != {"otherwise": {}}:
        raise ValueError(f"{where}: delta_con must be "
                         f"{{\"otherwise\": {{}}}} (Cadmium's default "
                         f"confluent transition)")
    ta = body["ta"]
    if not (isinstance(ta, dict) and list(ta) == ["otherwise"]
            and isinstance(ta["otherwise"], str)
            and states.get(ta["otherwise"]) == "R"):
        raise ValueError(f"{where}: ta must be {{\"otherwise\": "
                         f"\"<state variable of set R>\"}}, got {ta!r}")


def _check_coupled(name, body):
    """Keys, ports, components and coupling entries, before the component
    models are loaded. Returns the components as [{"model", "id"}]."""
    where = f"coupled '{name}'"
    _check_keys(where, body, COUPLED_KEYS, "coupled")
    _check_sets(where, body["x"], "input port")
    _check_sets(where, body["y"], "output port")
    if not isinstance(body["components"], dict):
        raise ValueError(f"{where}: \"components\" must be an object "
                         f"mapping each model name to a component id or a "
                         f"list of component ids")
    components = []
    for model, ids in body["components"].items():
        _check_name(where, "component model", model)
        if isinstance(ids, str):
            ids = [ids]  # the paper's form: one id per model
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(i, str) for i in ids)):
            raise ValueError(f"{where}: components of '{model}' must be a "
                             f"component id or a non-empty list of them: "
                             f"{ids!r}")
        for cid in ids:
            _check_name(where, "component id", cid)
            if cid == name or cid in body["components"]:
                raise ValueError(f"{where}: component id '{cid}' is also "
                                 f"a model name")
            if any(c["id"] == cid for c in components):
                raise ValueError(f"{where}: duplicate component id "
                                 f"'{cid}'")
            components.append({"model": model, "id": cid})

    for kind, keys in COUPLING_KEYS.items():
        if not isinstance(body[kind], list):
            raise ValueError(f"{where}: \"{kind}\" must be an array")
        seen = set()
        for c in body[kind]:
            if not (isinstance(c, dict) and set(c) == set(keys)
                    and all(isinstance(c[k], str) for k in keys)):
                raise ValueError(f"{where}: {kind} entry needs exactly the "
                                 f"string keys {keys}: {c!r}")
            # Cadmium rejects duplicate couplings too.
            key = tuple(c[k] for k in keys)
            if key in seen:
                raise ValueError(f"{where}: duplicate {kind} entry {c!r}")
            seen.add(key)
    return components


def _check_couplings(model):
    """Every coupling connects existing ports of the same set."""
    name, body = model["name"], model["body"]
    bodies = {c["id"]: model["models"][c["model"]]["body"]
              for c in model["components"]}

    def port(comp_id, port_name, side, kind):
        """The set of a port: comp_id None is the coupled model's own."""
        ports = body[side] if comp_id is None else \
            bodies.get(comp_id, {}).get(side)
        if ports is None:
            raise ValueError(f"coupled '{name}': {kind} references unknown "
                             f"component '{comp_id}'")
        if port_name not in ports:
            owner = name if comp_id is None else comp_id
            raise ValueError(f"coupled '{name}': {kind} references unknown "
                             f"port '{owner}.{port_name}'")
        return ports[port_name]

    for kind in COUPLING_KEYS:
        for c in model["body"][kind]:
            src = port(c.get("component_from"), c["port_from"],
                       "y" if "component_from" in c else "x", kind)
            dst = port(c.get("component_to"), c["port_to"],
                       "x" if "component_to" in c else "y", kind)
            if src != dst:
                raise ValueError(f"coupled '{name}': {kind} {c!r} connects "
                                 f"a {src} port to a {dst} port")


def _read_init(model):
    """{instance path: {state variable: value}} from the top model's
    <name>_init_state.json, for every atomic instance."""
    name = model["name"]
    filename = f"{name}_init_state.json"
    doc = _read_json(os.path.join(os.path.dirname(model["path"]), filename))
    if not (isinstance(doc, dict) and list(doc) == ["init_states"]
            and isinstance(doc["init_states"], dict)
            and list(doc["init_states"]) == [name]):
        raise ValueError(f"{filename}: expected {{\"init_states\": "
                         f"{{\"{name}\": {{...}}}}}}")
    init = {}

    def walk(m, node, path):
        if not isinstance(node, dict):
            raise ValueError(f"{filename}: entry for '{path or name}' must "
                             f"be an object")
        if m["kind"] == "atomic":
            expected, what = m["body"]["s"], "state variables"
        else:
            expected = {c["id"]: c["model"] for c in m["components"]}
            what = "components"
        missing = sorted(set(expected) - set(node))
        unknown = sorted(set(node) - set(expected))
        if missing or unknown:
            raise ValueError(f"{filename}: '{path or name}' needs exactly "
                             f"its {what} {list(expected)}; missing "
                             f"{missing}, unknown {unknown}")
        if m["kind"] == "atomic":
            for var, value in node.items():
                _check_value(f"{filename}: '{path}' {var}", value,
                             expected[var])
            init[path] = node
            return
        for cid, sub in node.items():
            walk(m["models"][expected[cid]], sub,
                 f"{path}.{cid}" if path else cid)

    walk(model, doc["init_states"][name],
         name if model["kind"] == "atomic" else "")
    return init


def model_name(doc, where):
    """The one model key of a parsed DEVSMap document."""
    if not isinstance(doc, dict):
        raise ValueError(f"{where}: top level must be an object")
    names = list(doc)
    if len(names) != 1:
        raise ValueError(f"{where}: expected exactly one top-level key, "
                         f"the model name, found {names}")
    _check_name(where, "model name", names[0])
    return names[0]


def load_model(path):
    """Load a DEVSMap model, its component models and its initial states.

    Returns the model dict described in the module docstring; raises
    ValueError on anything outside the profile."""
    model = _load(path, ())
    model["init"] = _read_init(model)
    return model


def _load(path, parents):
    filename = os.path.basename(path)
    data = _read_json(path)
    name = model_name(data, filename)
    kind = next((k for k in ("atomic", "coupled")
                 if filename == f"{name}_{k}.json"), None)
    if kind is None:
        raise ValueError(f"{filename}: model '{name}' must be in "
                         f"{name}_atomic.json or {name}_coupled.json")
    body = data[name]
    if not isinstance(body, dict):
        raise ValueError(f"{filename}: model '{name}' must be an object")
    model = {"name": name, "kind": kind, "path": os.path.abspath(path),
             "body": body}
    if kind == "atomic":
        _check_atomic(name, body)
        return model

    if name in parents:
        raise ValueError(f"coupled '{name}' contains itself")
    model["components"] = _check_coupled(name, body)
    folder = os.path.dirname(model["path"])
    model["models"] = {}
    for sub in dict.fromkeys(c["model"] for c in model["components"]):
        found = [p for p in (os.path.join(folder, f"{sub}_{k}.json")
                             for k in ("atomic", "coupled"))
                 if os.path.exists(p)]
        if len(found) != 1:
            raise ValueError(f"coupled '{name}': component model '{sub}' "
                             f"needs exactly one of {sub}_atomic.json and "
                             f"{sub}_coupled.json next to {filename}")
        model["models"][sub] = _load(found[0], parents + (name,))
    _check_couplings(model)
    return model


def flatten(model):
    """Dissolve nesting into leaf atomics and leaf-to-leaf couplings.

    `model` is a load_model result. Returns {"components", "couplings",
    "inputs", "outputs"}:
    - components: {path_id: {"model", "x", "y", "initial"}}, nested ids
      are "parent.child" paths; initial is the instance's init entry
    - couplings: [(src_id, out_port, dst_id, in_port)], IC resolved through
      nested EIC/EOC down to leaf atomic ports
    - inputs:  [(root_in_port, dst_id, in_port)]   from the root's EIC
    - outputs: [(src_id, out_port, root_out_port)] from the root's EOC
    An atomic flattens to itself: one component named after the model.
    Edges are not deduplicated: two hierarchical paths to the same leaf
    edge are two deliveries in Cadmium, so both are kept.
    """
    components, couplings = {}, []

    # Returns (ins, outs): port -> [(leaf_id, leaf_port)] at this boundary.
    def walk(m, path):
        body = m["body"]
        if m["kind"] == "atomic":
            components[path] = {"model": m["name"], "x": body["x"],
                                "y": body["y"], "initial": model["init"][path]}
            return ({p: [(path, p)] for p in body["x"]},
                    {p: [(path, p)] for p in body["y"]})

        child = {}
        for comp in m["components"]:
            cid = comp["id"]
            child[cid] = walk(m["models"][comp["model"]],
                              f"{path}.{cid}" if path else cid)

        for c in body["ic"]:
            for src in child[c["component_from"]][1][c["port_from"]]:
                for dst in child[c["component_to"]][0][c["port_to"]]:
                    couplings.append((*src, *dst))

        ins = {p: [] for p in body["x"]}
        for c in body["eic"]:
            ins[c["port_from"]] += child[c["component_to"]][0][c["port_to"]]
        outs = {p: [] for p in body["y"]}
        for c in body["eoc"]:
            outs[c["port_to"]] += child[c["component_from"]][1][c["port_from"]]
        return ins, outs

    ins, outs = walk(model, model["name"] if model["kind"] == "atomic" else "")
    return {
        "components": components,
        "couplings": sorted(couplings),
        "inputs": sorted((p, *d) for p, ds in ins.items() for d in ds),
        "outputs": sorted((*s, p) for p, ss in outs.items() for s in ss),
    }


# Key order is kept: condition maps are ordered, "otherwise" last.
def _compact(obj):
    return json.dumps(obj, separators=(",", ":"))


def _atomic_models(model, out=None):
    """{model name: atomic body} for every atomic model used in `model`."""
    out = {} if out is None else out
    if model["kind"] == "atomic":
        out[model["name"]] = model["body"]
    for sub in model.get("models", {}).values():
        _atomic_models(sub, out)
    return out


# Compact LLM input: structure + semantics, one definition per atomic model.
def flattened_summary(model):
    """Compact, deterministic text summary of the flattened model."""
    flat = flatten(model)
    lines = [f"Flattened DEVS summary of '{model['name']}'", "", "Components:"]
    for cid, c in sorted(flat["components"].items()):
        lines.append(f"- {cid}: model={c['model']}, "
                     f"in={sorted(c['x'])}, out={sorted(c['y'])}, "
                     f"initial={_compact(dict(sorted(c['initial'].items())))}")

    lines += ["", "Couplings:"]
    lines += [f"- {s}.{sp} -> {d}.{dp}" for s, sp, d, dp in flat["couplings"]]
    if not flat["couplings"]:
        lines.append("- (none)")

    lines += ["", "External ports:",
              f"- x (inputs):  {_compact(model['body']['x'])}",
              f"- y (outputs): {_compact(model['body']['y'])}"]
    lines += [f"- x.{p} -> {d}.{dp}" for p, d, dp in flat["inputs"]]
    lines += [f"- {s}.{sp} -> y.{p}" for s, sp, p in flat["outputs"]]

    lines += ["", "Atomic models (DEVSMap):"]
    for name, body in sorted(_atomic_models(model).items()):
        lines.append(f"- {name}: {_compact(body)}")

    lines += [
        "",
        "Resultant semantics:",
        "- time_advance = min over children of their remaining ta",
        "- imminent children (remaining ta equals that minimum) emit "
        "their outputs",
        "- outputs are routed through the couplings into input bags",
        "- imminent children take delta_int",
        "- children with input bags take delta_ext, where e is the time "
        "since their last transition (non-imminent children keep "
        "accumulating e)",
        "- if both happen together, use delta_con",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python devsmap.py "
                 "<name>_atomic.json|<name>_coupled.json")
    try:
        print(flattened_summary(load_model(sys.argv[1])))
    except ValueError as err:
        sys.exit(f"devsmap: {err}")
