"""Image providers: Pollinations (free online), Cloudflare Workers AI (free daily quota),
mflux (local FLUX on Apple Silicon), offline placeholder art."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import FONTS, load_settings


class ImageError(RuntimeError):
    pass


W, H = 768, 1344   # ~9:16, multiples of 16


def _save_bytes(data: bytes, out: Path) -> None:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ImageError(f"Provider did not return an image ({data[:120]!r})")
    img.convert("RGB").save(out, "PNG")


def _pollinations(prompt: str, seed: int, out: Path, s: dict) -> None:
    params = {"model": s.get("pollinations_image_model") or "flux", "width": W, "height": H,
              "seed": seed, "nologo": "true", "enhance": "false", "safe": "false"}
    key = s.get("pollinations_api_key")
    if key:
        url = f"https://gen.pollinations.ai/image/{quote(prompt, safe='')}"
        headers = {"Authorization": f"Bearer {key}"}
    else:  # legacy anonymous endpoint (rate limited, may be retired)
        url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
        headers = {}
    r = httpx.get(url, params=params, headers=headers, timeout=240, follow_redirects=True)
    if r.status_code >= 400:
        raise ImageError(f"Pollinations error {r.status_code}: {r.text[:200]}")
    _save_bytes(r.content, out)


def _cloudflare(prompt: str, seed: int, out: Path, s: dict) -> None:
    acct, token = s.get("cloudflare_account_id"), s.get("cloudflare_api_token")
    if not (acct and token):
        raise ImageError("Cloudflare account id / token not set (Settings)")
    model = s.get("cloudflare_image_model") or "@cf/black-forest-labs/flux-1-schnell"
    payload = {"prompt": prompt[:2000], "steps": 8}
    if "flux-1-schnell" not in model:  # this model's schema rejects a seed field
        payload["seed"] = seed % 2**31
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}",
                   headers={"Authorization": f"Bearer {token}"},
                   json=payload, timeout=180)
    if r.status_code >= 400:
        raise ImageError(f"Cloudflare error {r.status_code}: {r.text[:200]}")
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("image/"):
        _save_bytes(r.content, out)
        return
    img = (r.json().get("result") or {}).get("image")
    if not img:
        raise ImageError(f"Cloudflare returned no image: {r.text[:200]}")
    _save_bytes(base64.b64decode(img), out)


def _mflux_bin() -> str | None:
    cand = Path(sys.executable).parent / "mflux-generate"
    return str(cand) if cand.exists() else shutil.which("mflux-generate")


def _mflux(prompt: str, seed: int, out: Path, s: dict) -> None:
    exe = _mflux_bin()
    if not exe:
        raise ImageError("mflux not installed (run ./start.sh --local-images)")
    cmd = [exe, "--model", s.get("mflux_model") or "schnell", "--prompt", prompt,
           "--steps", str(s.get("mflux_steps") or 4), "--seed", str(seed % 2**31),
           "--width", "720", "--height", "1280", "--output", str(out)]
    if s.get("mflux_quantize"):
        cmd += ["-q", str(s["mflux_quantize"])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0 or not out.exists():
        raise ImageError(f"mflux failed: {(p.stderr or p.stdout)[-400:]}")


def _offline(prompt: str, seed: int, out: Path, s: dict) -> None:
    """Placeholder art so the whole pipeline can be tried without internet."""
    h = hashlib.sha256(f"{prompt}{seed}".encode()).digest()
    c1 = (40 + h[0] % 120, 20 + h[1] % 80, 60 + h[2] % 140)
    c2 = (200 + h[3] % 55, 120 + h[4] % 100, 40 + h[5] % 80)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        col = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = col
    d = ImageDraw.Draw(img)
    for i in range(14):  # a few glowing orbs
        r = 20 + h[(i + 6) % 32] % 90
        cx, cy = h[(i + 8) % 32] * W // 255, h[(i + 16) % 32] * H // 255
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 230, 170))
    img = img.filter(ImageFilter.GaussianBlur(18))
    d = ImageDraw.Draw(img)
    # a grid so camera motion is visible in tests
    for x in range(0, W, 96):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255), width=1)
    for y in range(0, H, 96):
        d.line([(0, y), (W, y)], fill=(255, 255, 255), width=1)
    font = ImageFont.truetype(str(FONTS / "Mukta_700Bold.ttf"), 38)
    parts = prompt.split(". ")
    text = "\n".join(textwrap.wrap((parts[1] if len(parts) > 1 else parts[0])[:160], 26))
    d.multiline_text((W // 2, H // 2), text, font=font, fill="white", anchor="mm", align="center",
                     stroke_width=3, stroke_fill="black")
    img.save(out, "PNG")


def _mflux_img2img(prompt: str, seed: int, out: Path, s: dict, ref_image: Path, strength: float) -> None:
    exe = _mflux_bin()
    if not exe:
        raise ImageError("mflux not installed (run ./start.sh --local-images)")
    cmd = [exe, "--model", s.get("mflux_model") or "schnell", "--prompt", prompt,
           "--steps", str(s.get("mflux_steps") or 4), "--seed", str(seed % 2**31),
           "--image", str(ref_image), str(strength),
           "--width", "720", "--height", "1280", "--output", str(out)]
    if s.get("mflux_quantize"):
        cmd += ["-q", str(s["mflux_quantize"])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0 or not out.exists():
        raise ImageError(f"mflux img2img failed: {(p.stderr or p.stdout)[-400:]}")


def generate_from_reference(prompt: str, seed: int, out: Path, ref_image: Path, strength: float = 0.55) -> str:
    """Repaint an uploaded reference photo into the scene's style, via mflux (local FLUX img2img).

    Only mflux supports image-conditioned generation here: Cloudflare's free tier doesn't expose
    a documented img2img schema, and Pollinations' free tier is text-to-image only.
    """
    s = load_settings()
    _mflux_img2img(prompt, seed, out, s, ref_image, max(0.05, min(0.95, strength)))
    return "mflux"


PROVIDERS = {"pollinations": _pollinations, "cloudflare": _cloudflare, "mflux": _mflux, "offline": _offline}


def _configured(name: str, s: dict) -> bool:
    return {"pollinations": True,
            "cloudflare": bool(s.get("cloudflare_account_id") and s.get("cloudflare_api_token")),
            "mflux": bool(_mflux_bin())}.get(name, False)


def generate(prompt: str, seed: int, out: Path, provider: str | None = None) -> tuple[str, list[str]]:
    s = load_settings()
    first = provider or s.get("image_provider", "pollinations")
    order = [first]
    if s.get("auto_fallback") and first != "offline":
        order += [p for p in ("pollinations", "cloudflare", "mflux") if p != first and _configured(p, s)]
    if os.environ.get("KATHA_TEST_FALLBACK"):
        order.append(os.environ["KATHA_TEST_FALLBACK"])
    forced = set(filter(None, os.environ.get("KATHA_FORCE_FAIL", "").split(",")))  # test hook
    errors = []
    for name in order:
        try:
            if name in forced:
                raise ImageError("forced failure (test)")
            PROVIDERS[name](prompt, seed, out, s)
            return name, errors
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    raise ImageError("; ".join(errors))
