"""Run DEVSMap models in Cadmium v2, via James's DEVSMap generator.

    simulate(path, end_time, build_dir, max_events) -> list of output events
    parse_trace(csv_path)                           -> list of output events

An event is {"time", "sender", "port", "value"}: one output of an atomic
component, as Cadmium logs it (sender is the component id).

The DEVSMap -> C++ step is NOT hand-written here.  James's
DEVSMap_to_Cadmium_Generator (vendored at third_party/devsmap_generator)
turns a directory of DEVSMap files into Cadmium headers; this module

  1. loads the model with devsmap.load_model (the profile's one reader and
     validator) and rejects the coupled forms the generator drops,
  2. stages the model and its component models (rewritten from the loaded
     bodies), one init state file and an experiment file in a temp
     directory (an atomic target gets a one-component wrapper coupled
     model, because the generator needs one),
  3. copies the generator in and applies the patch layer
     (james_patches.py: Cadmium v2 includes, C++ types for DEVSMap set
     names, per-instance initial states),
  4. compiles this repo's sim driver (cadmium/driver/main.cpp) against
     the generated headers and runs it,
  5. parses the CSV trace.

Every generator, compile or simulation failure is a CadmiumError; a model
outside the profile is devsmap's ValueError.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

import devsmap
import james_patches

_REPO = os.path.dirname(os.path.abspath(__file__))
_GENERATOR_DIR = os.environ.get(
    "DEVS_GENERATOR_DIR",
    os.path.join(_REPO, "third_party", "devsmap_generator"))
_DRIVER = os.environ.get(
    "CADMIUM_DRIVER", os.path.join(_REPO, "cadmium", "driver", "main.cpp"))
_CADMIUM_INCLUDE = os.environ.get(
    "CADMIUM_INCLUDE", os.path.join(_REPO, "cadmium_v2", "include"))

# An atomic target runs as component "top" of a coupled model "wrapper";
# devsmap reserves both names, so they cannot collide with user models.
_WRAPPER, _WRAPPED_ID = "wrapper", "top"
_TOO_MANY_EVENTS = 3  # sim_driver's exit code when --max-events is exceeded


class CadmiumError(Exception):
    """Generating, compiling or running a model in Cadmium failed."""


# --- staging ----------------------------------------------------------------

def _check_generator_scope(model):
    """Reject the coupled forms devsmap accepts but the generator drops: it
    emits only IC couplings, declares no coupled-model ports, and looks
    initial states up only under the top model."""
    if model["kind"] == "atomic":
        return
    where = f"coupled '{model['name']}'"
    if model["body"]["x"] or model["body"]["y"]:
        # devsmap checks that EIC/EOC use the model's own ports, so a
        # closed top model has no EIC/EOC either.
        raise CadmiumError(f"{where}: the Cadmium backend only runs closed "
                           f"top models (empty x and y, no EIC/EOC)")
    nested = [c["id"] for c in model["components"]
              if model["models"][c["model"]]["kind"] == "coupled"]
    if nested:
        raise CadmiumError(f"{where}: components {nested} are coupled "
                           f"models; the Cadmium backend only runs flat "
                           f"coupled models")


def _stage(model, end_time, in_dir):
    """Fill in_dir with exactly the files the generator reads. Returns the
    top model class name.

    Model files are rewritten from the loaded bodies as {name: body}: the
    generator takes a file's first key as the model name and reads with
    the platform's default encoding, so its input is the model key alone,
    in ASCII."""
    os.makedirs(in_dir)
    for m in (model, *model.get("models", {}).values()):
        devsmap.write_json(os.path.join(in_dir, os.path.basename(m["path"])),
                           {m["name"]: m["body"]})

    if model["kind"] == "coupled":
        top, top_file = model["name"], os.path.basename(model["path"])
        instances = {c["id"]: (model["models"][c["model"]],
                               model["init"][c["id"]])
                     for c in model["components"]}
    else:
        top, top_file = _WRAPPER, f"{_WRAPPER}_coupled.json"
        devsmap.write_json(os.path.join(in_dir, top_file), {_WRAPPER: {
            "x": {}, "y": {}, "components": {model["name"]: [_WRAPPED_ID]},
            "eic": [], "eoc": [], "ic": []}})
        instances = {_WRAPPED_ID: (model, model["init"][model["name"]])}

    # Patch P3 passes each component's values to its state constructor
    # positionally, so they are written in the atomic model's s order.
    init_file = f"{top}_init_state.json"
    devsmap.write_json(os.path.join(in_dir, init_file), {"init_states": {
        top: {cid: {var: values[var] for var in atomic["body"]["s"]}
              for cid, (atomic, values) in instances.items()}}})
    devsmap.write_json(os.path.join(in_dir, f"{top}_experiment.json"), {
        "model_under_test": {"model": top_file, "initial_state": init_file,
                             "parameters": ""},
        "experimental_frame": {}, "cpic": {}, "pocc": {},
        "time_span": repr(float(end_time))})
    return top


def _generate(stage):
    """Copy the generator into the staging dir, patch it, run it."""
    if not os.path.isdir(_GENERATOR_DIR):
        raise CadmiumError("DEVSMap generator not found (run `git submodule "
                           "update --init third_party/devsmap_generator` "
                           "or set DEVS_GENERATOR_DIR)")
    for py in glob.glob(os.path.join(_GENERATOR_DIR, "*.py")):
        shutil.copyfile(py, os.path.join(stage, os.path.basename(py)))
    os.makedirs(os.path.join(stage, "output", "main", "include"))
    try:
        james_patches.apply_patches(stage)
    except james_patches.PatchError as exc:
        raise CadmiumError(str(exc)) from None
    _run([sys.executable, "DEVSMap_parser.py"], "DEVSMap generator",
         timeout=120, cwd=stage)


# --- build, run, parse ------------------------------------------------------

def _without_local_dirs(text, *dirs):
    """`text` with the directories `dirs` cut from the front of paths."""
    for d in map(os.path.normpath, dirs):
        for form in (d, d.replace("\\", "/")):
            text = text.replace(form + os.sep, "").replace(form + "/", "")
    return text


def _run(cmd, what, timeout, cwd, allow=()):
    """Run cmd in cwd and return the CompletedProcess.  An exit code other
    than 0 or one in `allow` is a CadmiumError with up to 1500 characters
    of output, from a few lines before the first "error" line on; paths
    in it are shown relative to cwd or the repo."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CadmiumError(_without_local_dirs(
            f"{what} failed: {exc}", cwd, _REPO)) from None
    if r.returncode != 0 and r.returncode not in allow:
        lines = _without_local_dirs(r.stderr or r.stdout, cwd,
                                    _REPO).strip().splitlines()
        first = next((i for i, line in enumerate(lines)
                      if "error" in line.lower()), 0)
        output = "\n".join(lines[max(first - 3, 0):])[:1500]
        raise CadmiumError(f"{what} failed (exit {r.returncode}): "
                           f"{output or 'crashed without any output'}")
    return r


def simulate(path, end_time, build_dir=None, max_events=100_000):
    """Generate, compile and run a DEVSMap model; return its events.

    Builds in `build_dir` if given (kept for inspection, including the
    staged generator input under build_dir/stage), else in a fresh
    temporary directory removed afterwards.  A run whose trace would grow
    past `max_events` rows (outputs plus state changes) is a CadmiumError.
    """
    model = devsmap.load_model(path)
    _check_generator_scope(model)
    if build_dir is None:
        with tempfile.TemporaryDirectory(prefix="cadmium_",
                                         ignore_cleanup_errors=True) as tmp:
            return _build_and_run(model, end_time, tmp, max_events)
    return _build_and_run(model, end_time, build_dir, max_events)


def _build_and_run(model, end_time, build_dir, max_events):
    build_dir = os.path.abspath(build_dir)
    stage = os.path.join(build_dir, "stage")
    if os.path.exists(stage):
        shutil.rmtree(stage)  # a reused build dir gets a fresh staging area
    top = _stage(model, end_time, os.path.join(stage, "input"))
    _generate(stage)

    gen_include = os.path.join(stage, "output", "main", "include")
    if not os.path.exists(os.path.join(gen_include, top + ".hpp")):
        raise CadmiumError(f"DEVSMap generator produced no {top}.hpp")
    # The driver includes gen/top.hpp; "top" is reserved, so no generated
    # header shares that name (a quoted include would find itself).
    os.makedirs(os.path.join(build_dir, "gen"), exist_ok=True)
    with open(os.path.join(build_dir, "gen", "top.hpp"), "w",
              encoding="utf-8") as f:
        f.write(f'#pragma once\n#include "{top}.hpp"\n'
                f"using GenModel = {top};\n")

    cxx = os.environ.get("CXX", "g++")
    if not shutil.which(cxx):
        raise CadmiumError(f"C++ compiler {cxx!r} not found (set $CXX)")
    exe = os.path.join(build_dir, "sim_driver" + (".exe" if os.name == "nt"
                                                  else ""))
    # -static on Windows: never pick up a different libstdc++ DLL from PATH.
    static = ["-static"] if os.name == "nt" else []
    _run([cxx, "-std=c++17", "-O1", *static, "-I", _CADMIUM_INCLUDE,
          "-I", build_dir, "-I", gen_include, _DRIVER, "-o", exe],
         "compile", timeout=300, cwd=build_dir)

    trace = os.path.join(build_dir, "trace.csv")
    r = _run([exe, "--time", repr(float(end_time)), "--out", trace,
              "--max-events", str(max_events)],
             "simulation", cwd=build_dir, allow=(_TOO_MANY_EVENTS,),
             timeout=int(os.environ.get("DEVS_SIM_TIMEOUT", "60")))
    if r.returncode == _TOO_MANY_EVENTS:
        # the driver says "more than N trace rows before t=T"
        raise CadmiumError(f"too many events: {r.stderr.strip()}; check "
                           f"for a zero or negative time advance")
    return parse_trace(trace)


def parse_trace(csv_path):
    """Read a sim_driver CSV trace into output events, skipping state rows.

    Every output of every atomic component is an event, including those
    that only travel along internal couplings.  The sender is the
    component id Cadmium logged; values are taken verbatim (a fused
    model's outputs are plain values now, no sender prefix).  Times are
    rounded to 10 decimals.
    """
    events = []
    try:
        with open(csv_path, encoding="utf-8") as f:
            if not f.readline().startswith("time,"):
                raise ValueError("missing CSV header")
            for line in f:
                # Values may contain commas; only the first four separate.
                time, _, model, port, value = line.rstrip("\n").split(",", 4)
                if not port:
                    continue  # state row
                events.append({"time": round(float(time), 10),
                               "sender": model, "port": port,
                               "value": value})
    except (OSError, ValueError) as exc:
        raise CadmiumError(f"bad trace {os.path.basename(csv_path)}: "
                           f"{exc}") from None
    return events
