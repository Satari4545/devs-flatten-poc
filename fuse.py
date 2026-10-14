"""

Flow: coupled model + flattened summary -> Gemini -> proposed file
      -> differential test -> ACCEPT (keep) or REJECT (report).

Run:   python3 fuse.py
Self-test the verifier without an API key: python3 fuse.py --selftest
"""

import importlib.util
import os
import re
import sys
import time

from flatten import flattened_summary
from pingpong import PingPong
from verify import reference_trace, simulate, trace_json

COUPLED = "pingpong.py"
PROPOSAL = "fused_proposal.py"
ENV_FILE = ".env"

# Reference trace comes from running the trusted coupled model.
EXPECTED = reference_trace(10)

PROMPT = """\
You are given TWO compact views of the same DEVS model:

1. The readable coupled model describes its components and behavior.
2. The flattened summary describes its components, initial state,
couplings, and resultant semantics in a compact form.

Your task: combine these views into a MINIMAL fused atomic DEVS model with
the same observable behavior as the original.

Rules:
- Name the class FusedModel.
- Keep the five DEVS functions with exactly these names and signatures:
    time_advance(self)
    output_function_lambda(self) -> [(port, value)]
    internal_transition_function(self)
    external_transition_function(self, elapsed_time, input_bag)
    confluent_transition_function(self, input_bag)
- Use standard atomic DEVS output shape: a list of (port, value) pairs.
- Preserve the observable events and values exactly as represented by this
  JSON reference trace:
{expected}
- Look for invariants in the state, such as correlated variables or values
  that are impossible or redundant, and represent them with fewer meaningful
  state variables when possible.
- Preserve the model's external and confluent transition semantics. If the
  model is closed and has no external inputs, these functions may raise
  NotImplementedError.
- First explain the invariant(s) you found in plain words, then give ONE
  ```python block with the complete file. No other code blocks.

Readable coupled model:
```python
{coupled_source}
```

Flattened summary:
```text
{summary}
```
"""


def build_prompt():
    # Give the LLM code for meaning plus a compact flattened summary.
    with open(COUPLED, encoding="utf-8") as f:
        coupled_source = f.read()
    return PROMPT.format(
        coupled_source=coupled_source,
        summary=flattened_summary(PingPong()),
        expected=trace_json(EXPECTED),
    )


def load_env_file(path=ENV_FILE):
    """Load KEY=VALUE lines from .env without adding a dependency."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _event_text(event):
    """Best-effort text extraction from a streaming Interactions event."""
    for attr in ("output_text", "text", "delta"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(event, dict):
        for key in ("output_text", "text", "delta"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def call_llm(prompt):
    """Call Gemini Interactions API using streaming so progress is visible."""
    load_env_file()
    key = (os.environ.get("GEMINI_API_KEY")
           or os.environ.get("GOOGLE_API_KEY")
           or os.environ.get("LLM_API_KEY"))
    if not key:
        raise SystemExit(
            "set GEMINI_API_KEY first, e.g. export GEMINI_API_KEY=..."
        )
    try:
        from google import genai
    except ImportError as exc:
        raise SystemExit(
            "install the Gemini SDK first: pip install -U google-genai"
        ) from exc

    model_text = os.environ.get("GEMINI_MODELS") or os.environ.get(
        "GEMINI_MODEL", "gemini-3.8-flash"
    )
    models = [m.strip() for m in model_text.split(",") if m.strip()]
    retries = int(os.environ.get("GEMINI_RETRIES", "3"))
    client = genai.Client(api_key=key)

    last_error = None
    for model in models:
        for attempt in range(1, retries + 1):
            print(
                f"fuse: sending streaming request to Gemini model {model} "
                f"(attempt {attempt}/{retries})..."
            )
            chunks = []
            try:
                stream = client.interactions.create(
                    model=model,
                    input=prompt,
                    stream=True,
                )
                for event in stream:
                    text = _event_text(event)
                    if text:
                        print(text, end="", flush=True)
                        chunks.append(text)
                print()
                reply = "".join(chunks)
                if not reply.strip():
                    raise RuntimeError("Gemini stream ended without text output")
                print("fuse: Gemini request succeeded")
                return reply
            except Exception as exc:
                last_error = exc
                print(f"fuse: Gemini request failed: {exc}")
                if attempt < retries:
                    wait = 3 * attempt
                    print(f"fuse: retrying in {wait}s...")
                    time.sleep(wait)

    raise last_error


def extract_code(reply):
    # The model may explain first; only the Python block becomes the proposal.
    m = re.search(r"```python\n(.*?)```", reply, re.S)
    if not m:
        raise ValueError("no ```python block in the LLM reply")
    return m.group(1)


def find_atomic(mod):
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and hasattr(obj, "time_advance"):
            return obj
    return None


def verify_candidate(path):
    """Small deterministic judge for this PoC."""
    spec = importlib.util.spec_from_file_location("proposal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = find_atomic(mod)
    if cls is None:
        return False, "no atomic class found"
    missing = [m for m in ("time_advance", "output_function_lambda",
                           "internal_transition_function",
                           "external_transition_function",
                           "confluent_transition_function")
               if not hasattr(cls, m)]
    if missing:
        return False, f"missing functions: {missing}"
    got = simulate(cls(), 10)
    if got != EXPECTED:
        diffs = [(g, e) for g, e in zip(got, EXPECTED) if g != e][:3]
        return False, f"trace mismatch, first diffs: {diffs}"
    return True, f"trace matches ({len(got)} events)"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        # exercise the verifier on the known-good hand fusion; no API key needed
        ok, info = verify_candidate("fused_pingpong.py")
        print("selftest:", "ACCEPTED" if ok else "REJECTED", "-", info)
        raise SystemExit(0 if ok else 1)
    print("fuse: asking the LLM for the atomic model...")
    try:
        reply = call_llm(build_prompt())
        code = extract_code(reply)
    except Exception as exc:
        print(f"fuse: error occurred before writing proposal: {exc}")
        raise
    print("fuse: response received and Python code block extracted")
    with open(PROPOSAL, "w") as f:
        f.write(code)
    ok, info = verify_candidate(PROPOSAL)
    print("fuse:", "ACCEPTED" if ok else "REJECTED", "-", info)
    if not ok:
        print(f"proposal kept at {PROPOSAL} for inspection")
