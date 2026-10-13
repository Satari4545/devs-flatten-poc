"""Step 3 scaffolding: LLM-proposed fusion, verified before acceptance.

Flow: compiled_pingpong.py -> prompt -> LLM -> proposed file
      -> differential test -> ACCEPT (keep) or REJECT (report).

Setup: export LLM_API_KEY=...   (optional: LLM_MODEL, LLM_BASE_URL)
Run:   python3 fuse.py
Self-test the verifier without an API key: python3 fuse.py --selftest
"""

import importlib.util
import json
import os
import re
import sys
import urllib.request

COMPILED = "compiled_pingpong.py"
PROPOSAL = "fused_proposal.py"

# t=0.1 A sends, t=0.2 B sends, ... (alternating every 0.1)
EXPECTED = [(round(0.1 * t, 10), "A" if t % 2 == 1 else "B", "ball")
            for t in range(1, 11)]

PROMPT = """\
You are given a standalone DEVS atomic model (Python) that was mechanically
generated from a coupled model. Its state is the full product state
(one variable per component variable): correct but redundant.

Your task: rewrite it as a MINIMAL fused atomic with identical behavior.

Rules:
- Keep the five DEVS functions with exactly these names and signatures:
    time_advance(self)
    output_function_lambda(self) -> [(port, value)]
    internal_transition_function(self)
    external_transition_function(self, elapsed_time, input_bag)
    confluent_transition_function(self, input_bag)
- output_function_lambda returns (port, value) pairs; keep the sender
  identity inside the value exactly as the original does.
- Look for invariants in the state (correlated or constant variables)
  and collapse them into fewer, meaningful variables.
- Name the class FusedPingPong.
- Closed model: external/confluent may raise NotImplementedError.
- First explain the invariant you found in plain words, then give ONE
  ```python block with the complete file. No other code blocks.

The generated model:
```python
{source}
```
"""


def build_prompt():
    with open(COMPILED) as f:
        src = f.read()
    return PROMPT.format(source=src)


def call_llm(prompt):
    """Boilerplate chat-completions call. Key comes from the environment."""
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("set LLM_API_KEY first: export LLM_API_KEY=...")
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]


def extract_code(reply):
    m = re.search(r"```python\n(.*?)```", reply, re.S)
    if not m:
        raise ValueError("no ```python block in the LLM reply")
    return m.group(1)


def run_trace(obj, hits=10):
    t, trace = 0.0, []
    for _ in range(hits):
        t += obj.time_advance()
        for _port, val in obj.output_function_lambda():
            # convention: value carries (sender, payload), like the original
            s, v = val if isinstance(val, tuple) and len(val) == 2 else ("?", val)
            trace.append((round(t, 10), s, v))
        obj.internal_transition_function()
    return trace


def find_atomic(mod):
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and hasattr(obj, "time_advance"):
            return obj
    return None


def verify_candidate(path):
    """The cage: structural checks + differential test. Returns (ok, info)."""
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
    got = run_trace(cls())
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
    if not os.path.exists(COMPILED):
        raise SystemExit(f"run compiler.py first ({COMPILED} missing)")
    print("fuse: asking the LLM for a fused version...")
    reply = call_llm(build_prompt())
    with open(PROPOSAL, "w") as f:
        f.write(extract_code(reply))
    ok, info = verify_candidate(PROPOSAL)
    print("fuse:", "ACCEPTED" if ok else "REJECTED", "-", info)
    if not ok:
        print(f"proposal kept at {PROPOSAL} for inspection")
