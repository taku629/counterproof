"""Stress harness. Runs INSIDE the disposable sandbox container only.

Reads /src/job.json:
  {"mode": "stress"|"verify"|"smoke", "sol_lang": "python"|"cpp",
   "seeds": int, "max_size": int, "per_run_timeout": float, "input": str|None}
Files in /src: sol.(py|cpp), brute.py, gen.py
Prints one JSON object per line to stdout (events), last line is {"type":"result",...}.
"""
import json, os, shutil, subprocess, sys, time

SRC = "/src"
WORK = "/work"


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.strip().splitlines())


def run(cmd, stdin, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=WORK)
        status = "ok" if p.returncode == 0 else ("killed" if p.returncode < 0 else "re")
        return {"status": status, "code": p.returncode, "out": p.stdout[-20000:], "err": p.stderr[-2000:],
                "ms": int((time.time() - t0) * 1000)}
    except subprocess.TimeoutExpired as e:
        return {"status": "tle", "code": None, "out": (e.stdout or "")[-2000:] if isinstance(e.stdout, str) else "",
                "err": "", "ms": int(timeout * 1000)}


def prepare(job):
    os.makedirs(WORK, exist_ok=True)
    for f in os.listdir(SRC):
        shutil.copy(os.path.join(SRC, f), os.path.join(WORK, f))
    if job["sol_lang"] == "cpp":
        r = run(["g++", "-O2", "-std=c++17", "-o", "sol_bin", "sol.cpp"], "", 60)
        if r["status"] != "ok":
            emit({"type": "result", "verdict": "compile_error", "detail": r["err"]})
            sys.exit(0)
        return ["./sol_bin"]
    return [sys.executable, "sol.py"]


def main():
    job = json.load(open(os.path.join(SRC, "job.json")))
    sol = prepare(job)
    brute = [sys.executable, "brute.py"]
    tl = float(job.get("per_run_timeout", 2.0))

    if job["mode"] == "verify":
        inp = job["input"]
        a = run(sol, inp, tl)
        b = run(brute, inp, tl * 5)
        emit({"type": "result", "verdict": "verified" if (b["status"] == "ok" and (a["status"] != "ok" or norm(a["out"]) != norm(b["out"]))) else "not_reproduced",
              "input": inp, "sol": a, "brute": b})
        return

    seeds = int(job.get("seeds", 300))
    max_size = int(job.get("max_size", 8))
    tested = 0
    # small sizes first so the first counterexample is already small
    for size in range(1, max_size + 1):
        per_size = max(1, seeds // max_size)
        for k in range(per_size):
            seed = size * 100000 + k
            g = run([sys.executable, "gen.py", str(seed), str(size)], "", 5)
            if g["status"] != "ok":
                emit({"type": "result", "verdict": "gen_error", "detail": g["err"] or g["status"]})
                return
            inp = g["out"]
            b = run(brute, inp, tl * 5)
            if b["status"] != "ok":
                emit({"type": "result", "verdict": "brute_error", "input": inp, "detail": b["err"] or b["status"]})
                return
            a = run(sol, inp, tl)
            tested += 1
            if a["status"] != "ok" or norm(a["out"]) != norm(b["out"]):
                emit({"type": "result", "verdict": "counterexample", "tested": tested, "size": size,
                      "input": inp, "sol": a, "brute": b})
                return
            if tested % 25 == 0:
                emit({"type": "progress", "tested": tested, "size": size})
            if job["mode"] == "smoke" and tested >= 5:
                emit({"type": "result", "verdict": "smoke_ok", "tested": tested})
                return
    emit({"type": "result", "verdict": "no_counterexample", "tested": tested})


if __name__ == "__main__":
    main()
