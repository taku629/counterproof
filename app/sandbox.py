"""Disposable sandbox runner. Every call = one fresh container, destroyed afterwards.

Containment controls (all enforced by the host, never trusted to the code):
  --network none            no egress, no exfiltration
  --runtime runsc           gVisor user-space kernel (if installed) -> syscalls never hit the host kernel
  --read-only + tmpfs       root fs immutable, scratch space is RAM-only and capped
  --memory / --pids-limit   OOM and fork bombs absorbed
  --cpus                    CPU cap
  wall-clock timeout        container killed after N seconds
  --cap-drop ALL, no-new-privileges, uid 65534
  no env vars passed        API keys never enter the sandbox
"""
import json, os, shutil, subprocess, tempfile, time, uuid

IMAGE = os.getenv("SANDBOX_IMAGE", "counterproof-runner")
RUNTIME = os.getenv("SANDBOX_RUNTIME", "runsc")  # set to "" to use default runc
MEM = os.getenv("SANDBOX_MEM", "256m")
PIDS = os.getenv("SANDBOX_PIDS", "64")
CPUS = os.getenv("SANDBOX_CPUS", "1")


def run_job(files: dict, job: dict, wall_timeout: int = 90) -> dict:
    name = f"cp-{uuid.uuid4().hex[:12]}"
    d = tempfile.mkdtemp(prefix="cpjob-")
    try:
        for fn, content in files.items():
            with open(os.path.join(d, fn), "w") as f:
                f.write(content)
        with open(os.path.join(d, "job.json"), "w") as f:
            json.dump(job, f)
        os.chmod(d, 0o755)
        for fn in os.listdir(d):
            os.chmod(os.path.join(d, fn), 0o644)
        cmd = ["docker", "run", "--rm", "--name", name,
               "--network", "none", "--read-only",
               "--tmpfs", "/work:rw,exec,size=64m", "--tmpfs", "/tmp:rw,size=16m",
               "--memory", MEM, "--memory-swap", MEM, "--pids-limit", PIDS, "--cpus", CPUS,
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "-v", f"{d}:/src:ro"]
        if RUNTIME:
            cmd += ["--runtime", RUNTIME]
        cmd += [IMAGE]
        t0 = time.time()
        events, blast = [], []
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall_timeout, env={"PATH": os.environ.get("PATH", "")})
            out, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True)
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err, code = "", None
            blast.append({"kind": "wall_timeout", "detail": f"container killed after {wall_timeout}s"})
        for line in out.splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                pass
        if code == 137:
            blast.append({"kind": "oom_or_kill", "detail": f"container exited 137 (memory cap {MEM})"})
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result is None and code not in (0, None):
            blast.append({"kind": "sandbox_exhausted", "detail": f"untrusted code exhausted the container (exit {code}, pids cap {PIDS}); container torn down, host unaffected"})
        blast += classify(result)
        return {"container": name, "runtime": RUNTIME or "runc", "exit_code": code, "ms": int((time.time() - t0) * 1000),
                "result": result, "events": events, "stderr": err[-2000:], "blast": blast,
                "limits": {"network": "none", "memory": MEM, "pids": PIDS, "cpus": CPUS, "fs": "read-only", "timeout_s": wall_timeout},
                "destroyed": True}
    finally:
        shutil.rmtree(d, ignore_errors=True)


SIGNS = [
    ("network_blocked", ["Network is unreachable", "Temporary failure in name resolution", "Name or service not known", "ConnectionRefused", "gaierror"]),
    ("fs_write_blocked", ["Read-only file system", "Permission denied"]),
    ("fork_limited", ["Resource temporarily unavailable", "BlockingIOError", "can't start new thread", "fork"]),
    ("memory_capped", ["MemoryError", "std::bad_alloc", "Killed"]),
]


def classify(result):
    out = []
    if not result:
        return out
    for side in ("sol", "brute"):
        r = result.get(side) or {}
        txt = (r.get("err") or "")
        if r.get("status") == "tle":
            out.append({"kind": "time_limit", "detail": f"{side} killed at time limit"})
        if r.get("status") == "killed":
            out.append({"kind": "killed", "detail": f"{side} killed by signal {r.get('code')}"})
        for kind, keys in SIGNS:
            if any(k in txt for k in keys):
                out.append({"kind": kind, "detail": f"{side}: " + txt.strip().splitlines()[-1][:200] if txt.strip() else side})
    return out
