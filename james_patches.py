"""Patch layer for James's DEVSMap_to_Cadmium_Generator.

The generator (vendored unmodified under third_party/devsmap_generator) is
copied into a staging directory by cadmium_api.py, and the replacements
below are applied to the staged copies only.  Each entry is
(name, filename, why, [(anchor, replacement, expected_count), ...]) and
apply_patches() refuses to run if an anchor is not found exactly the
expected number of times, so an upstream change breaks loudly here
instead of silently generating wrong C++.

Patches:
  P1 cadmium-v2-includes   The generator emits the old Cadmium include
                           layout (cadmium/modeling/devs/...); the repo
                           pins Cadmium v2 (cadmium/core/modeling/...).
  P2 cpp-types             DEVSMap set names ("string", "R") are
                           emitted verbatim as C++ types and do not
                           compile.  Map the known set names to C++ types
                           in state members, port declarations and the
                           addInPort/addOutPort calls, and include
                           <string>/<limits> in the generated headers.
  P3 per-instance-init     The generator's initial-state support is a
                           global first-match search that gives every
                           instance of an atomic model the same initial
                           state (its parameterized variant is an
                           unimplemented stub).  Replace it: state
                           structs get a default constructor (per-type
                           defaults) plus a parameterized constructor,
                           atomic classes get a constructor taking an
                           initial state, and the coupled generator
                           emits addComponent<...>(id, ModelState(...))
                           with the component's entry of the staged
                           init_state file.  cadmium_api writes that file
                           from devsmap's validated initial states: one
                           complete entry per component, values in s
                           order, so the patch only translates "inf".
                           Components values may be one id (upstream
                           form) or a list of ids (the paper-form
                           extension this repo uses).

Known generator limitations that are NOT patched (stage-1 scope), and who
keeps models out of them:
  - only IC couplings are generated (no EIC/EOC), coupled models declare
    no ports, and initial states are only looked up under the top model:
    cadmium_api runs only closed, flat coupled models (or one atomic);
  - delta_con is not generated (Cadmium's default confluent transition,
    internal then external, applies) and ta only supports
    {"otherwise": "<state variable>"}: devsmap accepts only those forms;
  - a model file's first key is taken as the model name, and files are
    read with the platform's default encoding: cadmium_api stages each
    model file as {name: body}, written in ASCII;
  - the generator's own main.cpp is discarded; this repo's sim driver
    runs the generated headers instead.
"""

import os


class PatchError(Exception):
    """A patch anchor was not found in the staged generator copy."""


_TYPE_MAP_SRC = '''

TYPE_MAP = {"string": "std::string", "R": "double"}


def cpp_type(domain):
    """DEVSMap set name -> C++ type (unknown names pass through)."""
    return TYPE_MAP.get(domain, domain)
'''

_STATE_STRUCT_FN = '''

def generate_state_struct_per_instance(state_name, model):
    """State struct with a default constructor (per-type defaults) and a
    parameterized constructor taking every state variable, in s order.
    Instances of one atomic model can then start from different states:
    the coupled generator passes each component its own initial state."""
    defaults = {"std::string": '\\"\\"', "double": "0"}
    variables = list(model["s"].items())
    out = "struct " + state_name + " {\\n"
    for var, domain in variables:
        out += "\\t" + cpp_type(domain) + " " + var + ";\\n"
    out += "\\n\\t" + state_name + "(): "
    out += ", ".join(
        var + "(" + defaults.get(cpp_type(domain),
                                 cpp_type(domain) + "()") + ")"
        for var, domain in variables)
    out += " {\\n\\t}\\n"
    params = ", ".join(cpp_type(domain) + " " + var
                       for var, domain in variables)
    inits = ", ".join(var + "(" + var + ")" for var, domain in variables)
    out += "\\t" + state_name + "(" + params + "): " + inits
    out += " {\\n\\t}\\n};\\n\\n"
    return out
'''

_COUPLED_HELPERS = '''

def _component_init_args(data, coupled_name, comp_id):
    """C++ constructor arguments for one component's initial state: its
    init_state entry, which cadmium_api writes complete and in s order."""
    values = data["init_states"][coupled_name][comp_id].values()
    return ", ".join("std::numeric_limits<double>::infinity()"
                     if value == "inf" else value for value in values)
'''

_ADD_COMPONENT_OLD = """    # addComponent statements
    components = get_components(model)
    component_statements = ''
    for model_name, model_id in components.items():
        component_statements += '\\t\\tauto ' + model_id + ' = addComponent<' + model_name + '>("' + model_id + '");\\n'
    constructor += component_statements + '\\n'"""

_ADD_COMPONENT_NEW = """    # addComponent statements (P3: per-instance initial states; a
    # components value is one id or a list of ids)
    components = get_components(model)
    component_statements = ''
    for comp_model, comp_ids in components.items():
        if not isinstance(comp_ids, list):
            comp_ids = [comp_ids]
        for model_id in comp_ids:
            init_args = _component_init_args(data, model_name, model_id)
            component_statements += '\\t\\tauto ' + model_id + ' = addComponent<' + comp_model + '>("' + model_id + '", ' + comp_model + 'State(' + init_args + '));\\n'
    constructor += component_statements + '\\n'"""

_CTOR_OVERLOAD_OLD = """    port_initializations += '\\t}\\n\\n'
    return port_initializations"""

_CTOR_OVERLOAD_NEW = """    port_initializations += '\\t}\\n\\n'
    # P3: a second constructor that takes the initial state, so coupled
    # models can give each instance its own (see the coupled generator).
    _body = port_initializations.split('{\\n', 1)[1]
    port_initializations += ('\\t' + model_name + '(const std::string id, '
                             'const ' + state_name + '& initial_state) : '
                             'Atomic<' + state_name + '>(id, '
                             'initial_state) {\\n' + _body)
    return port_initializations"""

PATCHES = [
    ("P1", "generate_atomic_model_hpp.py",
     "Cadmium v2 include path for atomics",
     [("cadmium/modeling/devs/atomic.hpp",
       "cadmium/core/modeling/atomic.hpp", 1)]),
    ("P1+P3", "generate_coupled_model_hpp.py",
     "Cadmium v2 include path for coupled models; <limits> for infinity "
     "in per-instance initial states",
     [('return \'#include "cadmium/modeling/devs/coupled.hpp"\\n\'',
       'return \'#include "cadmium/core/modeling/coupled.hpp"\\n'
       '#include <limits>\\n\'', 1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "generated atomic headers include <string> and <limits>",
     [('return "#include <iostream>\\n"',
       'return "#include <iostream>\\n#include <string>\\n'
       '#include <limits>\\n"', 1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "TYPE_MAP/cpp_type for DEVSMap set names",
     [("import re\n", "import re\n" + _TYPE_MAP_SRC, 1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "state struct members use C++ types",
     [("'\\t' + variable_type + ' '", "'\\t' + cpp_type(variable_type) + ' '",
       1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "addInPort uses C++ types (patched before the port declarations: "
     "the declaration anchor is a substring of this one)",
     [("addInPort<' + data_type + '>",
       "addInPort<' + cpp_type(data_type) + '>", 1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "addOutPort uses C++ types (same ordering reason as addInPort)",
     [("addOutPort<' + data_type + '>",
       "addOutPort<' + cpp_type(data_type) + '>", 1)]),
    ("P2", "generate_atomic_model_hpp.py",
     "port declarations use C++ types",
     [("Port<' + data_type + '>", "Port<' + cpp_type(data_type) + '>", 2)]),
    ("P3", "generate_atomic_model_hpp.py",
     "state struct comes from the per-instance generator, not the "
     "first-match one",
     [("return generate_state_struct_no_parameters(init_states, "
       "state_name, model)",
       "return generate_state_struct_per_instance(state_name, model)", 1)]),
    ("P3", "generate_atomic_model_hpp.py",
     "atomic classes get a constructor taking an initial state",
     [(_CTOR_OVERLOAD_OLD, _CTOR_OVERLOAD_NEW, 1)]),
    ("P3", "generate_atomic_model_hpp.py",
     "the per-instance state struct generator itself (appended after "
     "the time-advance generator)",
     [("    return time_advance_function\n",
       "    return time_advance_function\n" + _STATE_STRUCT_FN, 1)]),
    ("P3", "generate_coupled_model_hpp.py",
     "thread the parsed data dict into coupled generation",
     [("generate_coupled_model(directory_cpp_code, coupled_model_name, "
       "coupled_model)",
       "generate_coupled_model(directory_cpp_code, coupled_model_name, "
       "coupled_model, data)", 1),
      ("def generate_coupled_model(directory, coupled_model_name, "
       "coupled_model):",
       "def generate_coupled_model(directory, coupled_model_name, "
       "coupled_model, data):", 1),
      ("generate_coupled_model_struct(coupled_model_name, coupled_model)",
       "generate_coupled_model_struct(coupled_model_name, coupled_model, "
       "data)", 1),
      ("def generate_coupled_model_struct(model_name, model):",
       "def generate_coupled_model_struct(model_name, model, data):", 1)]),
    ("P3", "generate_coupled_model_hpp.py",
     "addComponent passes each instance its own initial state",
     [(_ADD_COMPONENT_OLD, _ADD_COMPONENT_NEW, 1)]),
    ("P3", "generate_coupled_model_hpp.py",
     "init-state lookup helpers for the coupled generator (appended "
     "after the coupled struct generator)",
     [("    return constructor\n",
       "    return constructor\n" + _COUPLED_HELPERS, 1)]),
]


def apply_patches(directory):
    """Apply every patch to the generator copies staged in `directory`.

    Raises PatchError naming the patch if any anchor is missing or does
    not occur the expected number of times."""
    texts = {}
    for name, filename, why, replacements in PATCHES:
        path = os.path.join(directory, filename)
        if path not in texts:
            try:
                with open(path, encoding="utf-8") as f:
                    texts[path] = f.read()
            except OSError as exc:
                raise PatchError(f"patch {name} ({why}): cannot read "
                                 f"{filename}: {exc.strerror}") from None
        text = texts[path]
        for anchor, replacement, expected in replacements:
            found = text.count(anchor)
            if found != expected:
                raise PatchError(
                    f"patch {name} ({why}) failed on {filename}: anchor "
                    f"found {found} time(s), expected {expected}: "
                    f"{anchor[:80]!r}")
            text = text.replace(anchor, replacement)
        texts[path] = text
    for path, text in texts.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
