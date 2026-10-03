"""Paths, binaries and user settings (stored in data/settings.json)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[2]          # katha-studio/
BACKEND = ROOT / "backend"
DATA = Path(os.environ.get("KATHA_DATA", ROOT / "data"))
PROJECTS = DATA / "projects"
CACHE = DATA / "cache"
MUSIC = ROOT / "music"
FONTS = BACKEND / "fonts"
FRONTEND_DIST = ROOT / "frontend" / "dist"
SETTINGS_FILE = DATA / "settings.json"
DB_FILE = DATA / "katha.db"

for p in (DATA, PROJECTS, CACHE, MUSIC):
    p.mkdir(parents=True, exist_ok=True)


def _find_bin(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_BIN")
    if env:
        return env
    # Homebrew's full build (has libass) is keg-only; prefer it when present.
    for cand in (f"/opt/homebrew/opt/ffmpeg-full/bin/{name}", f"/usr/local/opt/ffmpeg-full/bin/{name}"):
        if Path(cand).exists():
            return cand
    return shutil.which(name) or name


FFMPEG = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")


def ffmpeg_capabilities() -> dict:
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return {"found": False}
    names = {line.split()[1] for line in out.splitlines() if len(line.split()) > 2 and line.startswith(" ")}
    need = ["zoompan", "xfade", "ass", "sidechaincompress", "loudnorm", "vignette"]
    return {"found": True, "path": FFMPEG, "filters": {n: n in names for n in need}}


DEFAULTS: dict = {
    # LLM (story -> scenes, titles)
    "llm_provider": "gemini",            # gemini | pollinations | ollama | offline
    "gemini_api_key": "",
    "gemini_model": "gemini-flash-latest",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:7b",
    "pollinations_api_key": "",
    "pollinations_text_model": "openai",
    # Images
    "image_provider": "pollinations",    # pollinations | cloudflare | mflux | offline
    "pollinations_image_model": "flux",
    "cloudflare_account_id": "",
    "cloudflare_api_token": "",
    "cloudflare_image_model": "@cf/black-forest-labs/flux-1-schnell",
    "mflux_model": "schnell",
    "mflux_steps": 4,
    "mflux_quantize": 8,
    # Voice
    "tts_provider": "edge",              # edge | offline
    # Try other configured providers automatically if the chosen one fails
    "auto_fallback": True,
}

_lock = Lock()


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return s


def save_settings(patch: dict) -> dict:
    with _lock:
        s = load_settings()
        for k, v in patch.items():
            if k in DEFAULTS and not (isinstance(v, str) and v.startswith("•")):
                s[k] = v
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
        return s


def public_settings() -> dict:
    """Settings with secrets masked, for the UI."""
    s = load_settings()
    for k in ("gemini_api_key", "pollinations_api_key", "cloudflare_api_token"):
        v = s.get(k) or ""
        s[k] = ("•" * 8 + v[-4:]) if v else ""
    return s
