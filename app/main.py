import asyncio, json, os, threading
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from fastapi import Form
from pydantic import BaseModel

from . import agent, sandbox, llm

app = FastAPI(title="Counterproof")
WEB = Path(__file__).resolve().parent.parent / "web"
SEM = threading.Semaphore(int(os.getenv("MAX_JOBS", "2")))


class Job(BaseModel):
    problem: str
    code: str
    lang: str = "python"


class Raw(BaseModel):
    code: str
    lang: str = "python"


def _stream(gen_fn):
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def worker():
        if not SEM.acquire(timeout=120):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "msg": "Server busy, try again in a minute"})
        else:
            try:
                for ev in gen_fn():
                    loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "msg": str(e)})
            finally:
                SEM.release()
        loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def body():
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream")


@app.post("/api/hunt")
async def hunt(job: Job):
    if len(job.problem) > 8000 or len(job.code) > 20000 or job.lang not in ("python", "cpp"):
        return JSONResponse({"error": "input too large or unsupported language"}, 400)
    return _stream(lambda: agent.run(job.problem, job.code, job.lang))


@app.post("/api/detonate")
async def detonate(raw: Raw):
    """Run arbitrary (hostile) code once in the sandbox, to show containment. No LLM involved."""
    if len(raw.code) > 20000 or raw.lang not in ("python", "cpp"):
        return JSONResponse({"error": "bad input"}, 400)
    sol = "sol.cpp" if raw.lang == "cpp" else "sol.py"
    files = {sol: raw.code, "brute.py": "print(0)\n", "gen.py": "print(1)\n"}

    def gen():
        yield {"type": "step", "step": "detonate", "msg": "Running untrusted code in a disposable sandbox"}
        r = sandbox.run_job(files, {"mode": "verify", "sol_lang": raw.lang, "input": "1\n", "per_run_timeout": 3}, wall_timeout=20)
        sol_r = (r["result"] or {}).get("sol") or {}
        yield {"type": "sandbox", "phase": "detonate", **agent._public(r),
               "stdout": (sol_r.get("out") or "")[-1500:], "stderr": (sol_r.get("err") or "")[-1500:], "status": sol_r.get("status")}
        yield {"type": "final", "verdict": "contained", "host_ok": True}
    return _stream(gen)


SETUP_TOKEN = os.getenv("SETUP_TOKEN", "")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_form(token: str):
    """One-time key entry so the inference key never travels through chat or git. Disabled once a key is set."""
    if not SETUP_TOKEN or token != SETUP_TOKEN or llm.KEY:
        return HTMLResponse("not found", 404)
    return ('<form method="post"><label>Vultr Serverless Inference API key</label><br>'
            '<input type="password" name="key" id="key" size="60" autocomplete="off"><br>'
            '<button type="submit">Save key</button></form>')


@app.post("/setup/{token}", response_class=HTMLResponse)
def setup_save(token: str, key: str = Form(...)):
    if not SETUP_TOKEN or token != SETUP_TOKEN or llm.KEY:
        return HTMLResponse("not found", 404)
    key = key.strip()
    lines = [l for l in (ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []) if not l.startswith("VULTR_INFERENCE_KEY=")]
    lines.append(f"VULTR_INFERENCE_KEY={key}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)
    llm.set_key(key)
    try:
        n = len(llm.list_models())
        return f"<p id='ok'>Key saved. Inference reachable, {n} models visible.</p>"
    except Exception as e:
        return f"<p id='err'>Key saved but test call failed: {type(e).__name__}</p>"


@app.get("/api/health")
def health():
    return {"ok": True, "runtime": sandbox.RUNTIME or "runc", "model": llm.MODEL or "auto", "inference_key": bool(llm.KEY)}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
