"""Counterproof agent loop.

1. PLAN   - LLM reads the problem, writes a brute-force reference (brute.py) and a seeded random
            generator (gen.py) that scales with a size argument.
2. SMOKE  - run 5 tiny cases in a fresh sandbox; if the generator or brute crashes, feed the error
            back to the LLM and repair (max 2 repairs).
3. STRESS - fresh sandbox, sizes 1..N, stop at the first mismatch -> smallest counterexample.
4. PROVE  - a *second*, independent fresh sandbox replays only that input. Only a reproduced
            mismatch is reported. The verdict is evidence from execution, never LLM opinion.
5. EXPLAIN- LLM explains the bug given the proven input/expected/actual.
Every step yields an event for the UI's live log, including the sandbox "Blast Log".
"""
from . import llm, sandbox

SYS = ("You are a competitive programming expert who writes test tooling. "
       "Reply with ONLY a JSON object, no prose.")

PLAN_PROMPT = """Problem statement:
<<<
{problem}
>>>

Write two Python 3 programs as a JSON object with keys "brute", "gen", "notes".
- "brute": reads the input from stdin exactly as the statement specifies and prints the correct answer.
  Use the most obvious exhaustive / simulation approach. Correctness over speed (inputs will be tiny).
- "gen": usage `python gen.py SEED SIZE`. Uses random.seed(SEED) and prints ONE valid input for the
  problem where all sizes/values are small and scale with SIZE (1..8): e.g. N <= SIZE+1, values in
  [-(SIZE*2), SIZE*3] when negatives are allowed. Include edge cases (N=1, equal values, zeros) often.
  Respect every constraint in the statement.
- "notes": one sentence on the key edge cases you target.
"""

REPAIR_PROMPT = """Your previous programs failed when run.
brute.py:
{brute}
gen.py:
{gen}
Failure: {failure}
Return the corrected JSON object with keys "brute", "gen", "notes"."""

EXPLAIN_PROMPT = """Problem:
{problem}
Contestant code ({lang}):
{code}
Proven counterexample input:
{inp}
Expected output: {exp}
Contestant output: {got}
In 3 short sentences: what the bug is, the line responsible, and a minimal fix. Plain text."""


def _ask(messages):
    text, model = llm.chat(messages)
    return llm.extract_json(text), model, text


def run(problem: str, code: str, lang: str, seeds: int = 400, max_size: int = 8):
    sol_name = "sol.cpp" if lang == "cpp" else "sol.py"
    yield {"type": "step", "step": "plan", "msg": "Asking Vultr Serverless Inference for a brute-force oracle and a generator"}
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": PLAN_PROMPT.format(problem=problem)}]
    try:
        plan, model, raw = _ask(msgs)
    except Exception as e:
        yield {"type": "error", "msg": f"LLM planning failed: {e}"}
        return
    yield {"type": "artifact", "model": model, "brute": plan["brute"], "gen": plan["gen"], "notes": plan.get("notes", "")}

    files = {}
    for attempt in range(3):
        files = {sol_name: code, "brute.py": plan["brute"], "gen.py": plan["gen"]}
        yield {"type": "step", "step": "smoke", "msg": f"Smoke test in a fresh sandbox (attempt {attempt + 1})"}
        r = sandbox.run_job(files, {"mode": "smoke", "sol_lang": lang, "max_size": 2, "seeds": 10, "per_run_timeout": 2})
        yield {"type": "sandbox", "phase": "smoke", **_public(r)}
        v = (r["result"] or {}).get("verdict")
        if v == "compile_error":
            yield {"type": "final", "verdict": "compile_error", "detail": r["result"].get("detail", "")}
            return
        if v in ("smoke_ok", "counterexample", "no_counterexample"):
            break
        failure = f"{v}: {(r['result'] or {}).get('detail') or r['stderr'] or 'no result'}"
        if attempt == 2:
            yield {"type": "error", "msg": f"Generator/brute could not be repaired: {failure}"}
            return
        yield {"type": "step", "step": "repair", "msg": f"Repairing tooling: {failure[:160]}"}
        msgs = [{"role": "system", "content": SYS},
                {"role": "user", "content": REPAIR_PROMPT.format(brute=plan["brute"], gen=plan["gen"], failure=failure[:1500])}]
        try:
            plan, model, raw = _ask(msgs)
        except Exception as e:
            yield {"type": "error", "msg": f"LLM repair failed: {e}"}
            return
        yield {"type": "artifact", "model": model, "brute": plan["brute"], "gen": plan["gen"], "notes": plan.get("notes", "")}

    yield {"type": "step", "step": "stress", "msg": f"Stress testing up to {seeds} random cases, smallest sizes first"}
    r = sandbox.run_job(files, {"mode": "stress", "sol_lang": lang, "max_size": max_size, "seeds": seeds, "per_run_timeout": 2}, wall_timeout=150)
    yield {"type": "sandbox", "phase": "stress", **_public(r)}
    res = r["result"] or {}
    if res.get("verdict") != "counterexample":
        yield {"type": "final", "verdict": res.get("verdict", "sandbox_failure"), "tested": res.get("tested"), "blast": r["blast"]}
        return

    inp = res["input"]
    yield {"type": "step", "step": "prove", "msg": "Replaying the counterexample in a second, independent sandbox"}
    r2 = sandbox.run_job(files, {"mode": "verify", "sol_lang": lang, "input": inp, "per_run_timeout": 2})
    yield {"type": "sandbox", "phase": "prove", **_public(r2)}
    res2 = r2["result"] or {}
    if res2.get("verdict") != "verified":
        yield {"type": "final", "verdict": "flaky", "input": inp}
        return

    exp, got = res2["brute"]["out"].strip(), res2["sol"]["out"].strip() or f"<{res2['sol']['status']}>"
    explanation = ""
    try:
        explanation, _ = llm.chat([{"role": "user", "content": EXPLAIN_PROMPT.format(
            problem=problem[:3000], lang=lang, code=code[:4000], inp=inp, exp=exp, got=got)}], max_tokens=2000)
    except Exception as e:
        explanation = f"(explanation unavailable: {e})"
    yield {"type": "final", "verdict": "counterexample", "input": inp, "expected": exp, "got": got,
           "sol_status": res2["sol"]["status"], "tested": res.get("tested"), "size": res.get("size"),
           "proof_container": r2["container"], "explanation": explanation.strip()}


def _public(r):
    return {k: r[k] for k in ("container", "runtime", "exit_code", "ms", "blast", "limits", "destroyed")} | {
        "verdict": (r["result"] or {}).get("verdict"), "tested": (r["result"] or {}).get("tested")}
