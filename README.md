# Counterproof — proven counterexamples, zero blast radius

**Live demo:** http://45.32.38.8 (Vultr Cloud Compute, Tokyo)
**Challenge:** Vultr Agent Rush — 01 Blast Radius Zero

Paste a competitive-programming problem and your solution. An AI agent writes a brute-force oracle and a random input generator, stress-tests your code, and returns the **smallest input that breaks it — proven by re-execution in a second, independent sandbox**. Every byte of untrusted code (yours *and* the LLM's) runs in a disposable, network-less gVisor container that is destroyed afterwards.

The verdict is evidence from execution, never LLM opinion.

## Why

- "Wrong Answer" with no failing case is the most frustrating moment in competitive programming, coding interviews and programming classes. Judges hide their tests.
- LLMs are good at writing a slow-but-obvious reference solution and a generator, but bad at *judging* code by reading it. Counterproof uses the LLM only for what it is good at, and lets execution decide.
- Running LLM-written and user-submitted code is exactly the agent risk this challenge is about: infinite loops, fork bombs, exfiltration, `rm -rf`. Counterproof treats all of it as hostile.

## How it works

```
Browser ──SSE──▶ FastAPI on Vultr VM (Tokyo)
                   │
                   ├─ PLAN    Vultr Serverless Inference (qwen3.8-27b, OpenAI-compatible)
                   │          → brute.py (oracle) + gen.py (seeded generator)
                   ├─ SMOKE   sandbox #1: 5 tiny cases; on crash → error fed back to LLM, repair (≤2)
                   ├─ STRESS  sandbox #2: sizes 1..8, smallest first, stop at first mismatch
                   ├─ PROVE   sandbox #3: replay only that input; report only if it reproduces
                   └─ EXPLAIN Vultr Serverless Inference: bug, line, minimal fix
```

### Sandbox (app/sandbox.py, sandbox/stress.py)

Each job = one fresh container, `docker run --rm`, destroyed afterwards.

| Control | Setting | Stops |
|---|---|---|
| `--runtime runsc` | gVisor user-space kernel | syscalls never reach the host kernel |
| `--network none` | no interfaces | exfiltration, downloads, C2 |
| `--read-only` + tmpfs | immutable root, 64 MB RAM scratch | `rm -rf /`, writes to `/etc` |
| `--memory 256m`, per-child `RLIMIT_AS` 160 MB | | memory bombs |
| `--pids-limit 64`, per-child `RLIMIT_NPROC` | | fork bombs |
| `--cpus 1`, per-run timeout + process-group kill, wall timeout | | infinite loops |
| `--cap-drop ALL`, `no-new-privileges`, uid 65534 | | privilege escalation |
| no env vars passed | | the inference API key never enters a sandbox |

Every run emits a **Blast Log**: which limits applied and which ones fired (`network_blocked`, `memory_capped`, `time_limit`, `fs_write_blocked`, `sandbox_exhausted`, …).

### Detonate tab

Runs hostile code once and shows what was contained: infinite loop, fork bomb, env exfiltration over HTTP, `rm -rf /` + write `/etc/hosts`, memory bomb. The host keeps serving requests.

## Vultr services used

- **Vultr Serverless Inference** — all LLM calls (planning, repair, explanation). Model: `qwen3.8-27b`. Base URL `https://api.vultrinference.com/v1`.
- **Vultr Cloud Compute** — `vc2-1c-2gb`, Ubuntu 24.04, Tokyo. Runs the FastAPI backend, Docker and gVisor.

## Run it yourself

```bash
# on a fresh Ubuntu 24.04 VM, as root
git clone https://github.com/taku629/counterproof /opt/counterproof
cd /opt/counterproof && bash deploy/setup.sh      # docker, gVisor, image, venv, systemd
# then open http://<ip>/setup/<SETUP_TOKEN from .env> once to store the inference key
# (or put VULTR_INFERENCE_KEY=... in /opt/counterproof/.env and restart the service)
```

Environment (`.env`): `VULTR_INFERENCE_KEY`, `VULTR_MODEL` (default `qwen3.8-27b`), `SANDBOX_RUNTIME` (`runsc` or empty for runc), `SANDBOX_MEM`, `SANDBOX_PIDS`, `MAX_JOBS`.

## API

- `POST /api/hunt` `{problem, code, lang: "python"|"cpp"}` → server-sent events
- `POST /api/detonate` `{code, lang}` → server-sent events
- `GET /api/health`

## Use cases

- Competitive programmers stuck on "WA" with hidden tests.
- Coding-interview and bootcamp platforms: give candidates/students a failing case instead of a red X.
- Teaching: instructors check student submissions against an oracle safely.
- Any product that must execute LLM-generated code: the sandbox layer is reusable.

## Limits

- No counterexample in N cases is not a proof of correctness.
- The oracle is LLM-written; a wrong oracle would surface as a mismatch, so the explanation shows both programs and the proof step re-runs them independently.

Built solo for the Vultr Agent Rush hackathon (lablab.ai), November 2026.
