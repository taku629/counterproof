"""Vultr Serverless Inference client (OpenAI-compatible). The key lives only on the host, never in a sandbox."""
import json, os, re
import httpx

BASE = os.getenv("VULTR_INFERENCE_BASE", "https://api.vultrinference.com/v1")
KEY = os.getenv("VULTR_INFERENCE_KEY", "")
MODEL = os.getenv("VULTR_MODEL", "")


def list_models():
    r = httpx.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def chat(messages, temperature=0.2, max_tokens=4000):
    model = MODEL or (list_models() or [""])[0]
    r = httpx.post(f"{BASE}/chat/completions", timeout=180,
                   headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                   json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], model


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or fences; take the outermost object."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])
