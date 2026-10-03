"""Text LLM providers: Gemini (free tier), Pollinations, Ollama (local), offline heuristic."""
from __future__ import annotations

import json
import re

import httpx

from ..config import load_settings


class LLMError(RuntimeError):
    pass


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise LLMError("The model did not return valid JSON")


# ---------- Gemini ----------
GEMINI = "https://generativelanguage.googleapis.com/v1beta"


def gemini_models(key: str) -> list[str]:
    r = httpx.get(f"{GEMINI}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(m["name"].split("/", 1)[-1])
    return out


def _pick_flash(models: list[str]) -> str | None:
    cands = [m for m in models if "flash" in m and "image" not in m and "tts" not in m
             and "live" not in m and "audio" not in m and "lite" not in m]
    if not cands:
        return None
    def ver(m: str):
        nums = re.findall(r"\d+(?:\.\d+)?", m)
        return (float(nums[0]) if nums else 0, "preview" not in m and "exp" not in m)
    return sorted(cands, key=ver)[-1]


def _gemini(system: str, user: str, s: dict) -> dict:
    key = s.get("gemini_api_key")
    if not key:
        raise LLMError("Gemini API key not set (Settings)")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
    }
    model = s.get("gemini_model") or "gemini-flash-latest"
    for attempt in range(2):
        r = httpx.post(f"{GEMINI}/models/{model}:generateContent", json=body,
                       headers={"x-goog-api-key": key}, timeout=120)
        if r.status_code == 404 and attempt == 0:
            picked = _pick_flash(gemini_models(key))
            if not picked:
                break
            model = picked
            continue
        if r.status_code >= 400:
            raise LLMError(f"Gemini error {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return _parse_json(text)
    raise LLMError(f"Gemini model '{model}' not found")


# ---------- Pollinations (OpenAI-compatible) ----------
def _pollinations(system: str, user: str, s: dict) -> dict:
    headers = {}
    if s.get("pollinations_api_key"):
        headers["Authorization"] = f"Bearer {s['pollinations_api_key']}"
    body = {
        "model": s.get("pollinations_text_model") or "openai",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    r = httpx.post("https://gen.pollinations.ai/v1/chat/completions", json=body, headers=headers, timeout=180)
    if r.status_code >= 400:
        raise LLMError(f"Pollinations error {r.status_code}: {r.text[:300]}")
    return _parse_json(r.json()["choices"][0]["message"]["content"])


# ---------- Ollama (local) ----------
def _ollama(system: str, user: str, s: dict) -> dict:
    url = (s.get("ollama_url") or "http://localhost:11434").rstrip("/")
    body = {
        "model": s.get("ollama_model") or "qwen2.5:7b",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": "json", "stream": False, "options": {"temperature": 0.8},
    }
    try:
        r = httpx.post(f"{url}/api/chat", json=body, timeout=600)
    except httpx.ConnectError:
        raise LLMError("Ollama is not running (install from ollama.com and run `ollama serve`)")
    if r.status_code >= 400:
        raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
    return _parse_json(r.json()["message"]["content"])


PROVIDERS = {"gemini": _gemini, "pollinations": _pollinations, "ollama": _ollama}


def _configured(name: str, s: dict) -> bool:
    return {"gemini": bool(s.get("gemini_api_key")),
            "pollinations": bool(s.get("pollinations_api_key")),
            "ollama": True}.get(name, False)


def generate_json(system: str, user: str) -> tuple[dict | None, str, list[str]]:
    """Returns (data or None, provider used, errors). None means use the offline fallback."""
    s = load_settings()
    first = s.get("llm_provider", "gemini")
    if first == "offline":
        return None, "offline", []
    order = [first] + ([p for p in PROVIDERS if p != first and _configured(p, s)] if s.get("auto_fallback") else [])
    errors = []
    for name in order:
        try:
            return PROVIDERS[name](system, user, s), name, errors
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    return None, "offline", errors
