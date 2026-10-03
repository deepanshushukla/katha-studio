"""FFmpeg composition: Ken Burns clips -> crossfades -> grade -> captions -> narration + ducked music."""
from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import base64
from html import escape

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JavascriptLexer

from ..config import CACHE, FFMPEG, FONTS, MUSIC
from .captions import build_ass

FPS = 30
OUT_W, OUT_H = 1080, 1920
WORK_SCALE = 2                     # zoompan on a 2x image -> smooth sub-pixel motion

TRANSITIONS = ["fade", "dissolve", "smoothleft", "smoothup", "circleopen", "fadeblack", "radial", "slideleft", "wipeup"]
MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_up", "zoom_in_top", "pan_left", "pan_down"]

DEFAULT_OPTIONS = {
    "captions": "karaoke", "highlight": "gold", "caption_position": "lower",
    "show_title": True, "title_text": "",
    "music": "", "music_volume": 0.16,
    "transition": "auto", "transition_duration": 0.5,
    "motion": "normal",            # gentle | normal | strong
    "vignette": True, "warm": True, "grain": False,
    "scene_pause": 0.12,
    "visual_mode": "scenes",       # scenes | background
    "background": "",              # key into BACKGROUND_PRESETS
}

# ---------- predefined ambient background loops (whole-video visual mode) ----------
BACKGROUND_PRESETS = {
    "golden_glow": {"label": "Golden glow", "top": (26, 14, 40), "bottom": (198, 108, 28),
                    "orb": (255, 214, 133), "motion": "zoom_in"},
    "twilight_indigo": {"label": "Twilight indigo", "top": (7, 9, 28), "bottom": (52, 40, 94),
                        "orb": (172, 190, 255), "motion": "pan_right"},
}
BG_LOOP_SECONDS = 20
BG_DIR = CACHE / "backgrounds"


def _background_still(preset: dict, seed: int = 7) -> Image.Image:
    """Small blurred gradient + glow orbs, upscaled — cheap to generate, looks identical to a full-res render."""
    w, h = OUT_W // 4, OUT_H // 4
    top, bottom = preset["top"], preset["bottom"]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        row = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(10):
        r = rnd.randint(20, 70)
        cx, cy = rnd.randint(0, w), rnd.randint(0, h)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=preset["orb"])
    img = img.filter(ImageFilter.GaussianBlur(16))
    return img.resize((OUT_W * WORK_SCALE, OUT_H * WORK_SCALE), Image.LANCZOS)


def ensure_background(key: str) -> Path:
    """Build (once) and cache a seamless looping ambient background video for a preset."""
    if key not in BACKGROUND_PRESETS:
        key = next(iter(BACKGROUND_PRESETS))
    BG_DIR.mkdir(parents=True, exist_ok=True)
    out = BG_DIR / f"{key}.mp4"
    thumb = BG_DIR / f"{key}.jpg"
    if out.exists() and thumb.exists():
        return out
    preset = BACKGROUND_PRESETS[key]
    still = _background_still(preset)
    still.resize((OUT_W, OUT_H), Image.LANCZOS).save(thumb, "JPEG", quality=90)
    still_path = BG_DIR / f"{key}_still.jpg"
    still.save(still_path, "JPEG", quality=95)
    half = BG_LOOP_SECONDS / 2
    frames = round(half * FPS)
    fwd = BG_DIR / f"{key}_fwd.mp4"
    _run([FFMPEG, "-y", "-v", "error", "-i", str(still_path), "-vf",
          _zoompan(preset["motion"], frames, 0.06) + ",format=yuv420p",
          "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(fwd)])
    # boomerang (forward + reverse) so the loop point is a perfect frame match — no jump cut
    _run([FFMPEG, "-y", "-v", "error", "-i", str(fwd), "-filter_complex",
          "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0,format=yuv420p[vout]",
          "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(out)])
    fwd.unlink(missing_ok=True)
    still_path.unlink(missing_ok=True)
    return out


def list_backgrounds() -> list[dict]:
    out = []
    for key, preset in BACKGROUND_PRESETS.items():
        ensure_background(key)
        thumb = BG_DIR / f"{key}.jpg"
        out.append({"key": key, "label": preset["label"], "thumb_path": str(thumb)})
    return out

MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def list_music() -> list[str]:
    return sorted(p.name for p in MUSIC.iterdir() if p.suffix.lower() in MUSIC_EXT)


class RenderError(RuntimeError):
    pass


def warm_grade(img: Image.Image) -> Image.Image:
    """Golden 'divine' grade, baked into the still (far cheaper than grading every video frame)."""
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.05 + 3)))
    b = b.point(lambda v: int(v * 0.93))
    img = Image.merge("RGB", (r, g, b))
    img = ImageEnhance.Color(img).enhance(1.08)
    return ImageEnhance.Contrast(img).enhance(1.04)


PYGMENTS_STYLE_FOR_THEME = {"dark": "native", "light": "default", "dracula": "dracula", "monokai": "monokai"}

_heading_font_data_uri: str | None = None


def _heading_font_uri() -> str:
    """Base64 data: URI for the heading font — Chromium blocks @font-face file:// URLs loaded
    from page.set_content() pages (no file-origin), so the bytes must be inlined directly."""
    global _heading_font_data_uri
    if _heading_font_data_uri is None:
        data = (FONTS / "Mukta_800ExtraBold.ttf").read_bytes()
        _heading_font_data_uri = "data:font/ttf;base64," + base64.b64encode(data).decode("ascii")
    return _heading_font_data_uri


def _screenshot_html(html: str, width: int, height: int) -> Image.Image:
    """Render an HTML page with a headless browser and screenshot the `.box` element, cropped to
    its actual rendered size. Shared by the heading, code-block, and raw-HTML overlay renderers."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html)
            page.evaluate("document.fonts.ready")  # wait for any @font-face to finish loading
            png_bytes = page.locator(".box").screenshot()
        finally:
            browser.close()
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def _render_code_html(code: str, theme_key: str, base_size: int, max_width: int, max_height: int) -> Image.Image:
    """Render a syntax-highlighted code block via headless Chromium (real CSS text wrapping instead
    of hand-rolled char counting), cropped to its actual rendered size."""
    style_name = PYGMENTS_STYLE_FOR_THEME.get(theme_key, "native")
    formatter = HtmlFormatter(style=style_name, noclasses=True, nowrap=True)
    body_html = highlight(code, JavascriptLexer(), formatter)
    bg = formatter.style.background_color or "#18141a"
    pad = 48 * WORK_SCALE
    font_size = max(18, base_size) * WORK_SCALE
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html, body {{ margin: 0; padding: 0; background: transparent; }}
      .box {{
        display: inline-block; box-sizing: border-box;
        max-width: {max_width}px; max-height: {max_height}px; overflow: hidden;
        background: {bg}; border-radius: {24 * WORK_SCALE}px; padding: {pad}px;
        border: 3px solid rgba(255,255,255,0.15);
        font-family: ui-monospace, Menlo, Consolas, monospace; font-size: {font_size}px; line-height: 1.5;
        white-space: pre-wrap; word-break: break-word;
      }}
    </style></head><body><div class="box">{body_html}</div></body></html>"""
    return _screenshot_html(html, max_width + 2 * pad + 40, max_height + 40)


def draw_code_block(img: Image.Image, code: str, theme_key: str = "dark", base_size: int = 44) -> Image.Image:
    """Paste a syntax-highlighted code block onto img, rendered via a headless browser so real CSS
    handles text wrapping instead of hand-rolled character counting. Clamped to a fixed vertical
    band (content is cropped, not shrunk, if it's taller than the band). No-op if code is blank."""
    if not code.strip():
        return img
    pad = 48 * WORK_SCALE
    max_width = img.width - 2 * pad
    max_height = int(img.height * 0.42)
    box = _render_code_html(code, theme_key, base_size, max_width, max_height)
    img = img.convert("RGBA")
    pos = ((img.width - box.width) // 2, int(img.height * 0.40))
    img.alpha_composite(box, pos)
    return img.convert("RGB")


def draw_content_image(img: Image.Image, content_image_path: str) -> Image.Image:
    """Composite a per-beat override image (math/diagram, etc.) into the same band the text heading
    would occupy, in place of it — scaled to fit, aspect preserved. No-op if the path is blank."""
    if not content_image_path:
        return img
    pad = 60 * WORK_SCALE
    max_height = int(img.height * 0.32)
    max_width = img.width - 2 * pad
    overlay_img = Image.open(content_image_path).convert("RGBA")
    scale = min(max_width / overlay_img.width, max_height / overlay_img.height)
    size = (max(1, int(overlay_img.width * scale)), max(1, int(overlay_img.height * scale)))
    overlay_img = overlay_img.resize(size, Image.LANCZOS)
    img = img.convert("RGBA")
    pos = ((img.width - size[0]) // 2, int(90 * WORK_SCALE))
    img.alpha_composite(overlay_img, pos)
    return img.convert("RGB")


def draw_beat_heading(img: Image.Image, text: str) -> Image.Image:
    """Paste the beat's question/answer text as a multi-line heading near the top, rendered via a
    headless browser so real CSS text wrapping handles layout — clamped to a fixed vertical band
    (content is cropped, not shrunk, if it's taller than the band). No-op if text is blank."""
    if not text.strip():
        return img
    pad = 60 * WORK_SCALE
    max_height = int(img.height * 0.32)
    size = 54 * WORK_SCALE
    font_url = _heading_font_uri()
    safe = "<br>".join(escape(line) for line in text.strip().splitlines())
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      @font-face {{ font-family: Heading; src: url('{font_url}'); }}
      html, body {{ margin: 0; padding: 0; background: transparent; }}
      .box {{
        width: {img.width}px; box-sizing: border-box; max-height: {max_height}px; overflow: hidden;
        background: rgba(10, 8, 20, 0.745); padding: {pad}px;
        font-family: Heading, sans-serif; font-size: {size}px; line-height: 1.35; color: #fff;
        text-align: center; word-break: break-word;
      }}
    </style></head><body><div class="box">{safe}</div></body></html>"""
    box = _screenshot_html(html, img.width, max_height + 2 * pad)
    img = img.convert("RGBA")
    img.alpha_composite(box, (0, int(90 * WORK_SCALE)))
    return img.convert("RGB")


def draw_custom_html(img: Image.Image, html_fragment: str) -> Image.Image:
    """Composite the user's own raw HTML into the same band the text heading would occupy, in place
    of it — rendered on a transparent background so it sits directly on the trademark/background
    image, same as the other heading options. No-op if blank."""
    if not html_fragment.strip():
        return img
    pad = 20 * WORK_SCALE
    max_height = int(img.height * 0.32)
    max_width = img.width - 2 * pad
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html, body {{ margin: 0; padding: 0; background: transparent; }}
      .box {{
        display: inline-block; box-sizing: border-box;
        max-width: {max_width}px; max-height: {max_height}px; overflow: hidden;
      }}
    </style></head><body><div class="box">{html_fragment}</div></body></html>"""
    box = _screenshot_html(html, max_width + 2 * pad, max_height + 2 * pad)
    img = img.convert("RGBA")
    pos = ((img.width - box.width) // 2, int(90 * WORK_SCALE))
    img.alpha_composite(box, pos)
    return img.convert("RGB")


def fit_vertical(src: Path, dst: Path, warm: bool = False, overlay: dict | None = None) -> None:
    """Make a 9:16 image at 2x output size: crop if close to 9:16, otherwise blurred-background letterbox.
    overlay (code-quiz beats only): {"show_title_card": bool, "title_text": str, "code_text": str}."""
    W, H = OUT_W * WORK_SCALE, OUT_H * WORK_SCALE
    img = Image.open(src).convert("RGB")
    r_img, r_out = img.width / img.height, W / H
    if abs(r_img - r_out) / r_out < 0.12:
        scale = max(W / img.width, H / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        left, top = (img.width - W) // 2, (img.height - H) // 2
        img = img.crop((left, top, left + W, top + H))
    else:
        bg_scale = max(W / img.width, H / img.height)
        bg = img.resize((round(img.width * bg_scale), round(img.height * bg_scale)), Image.LANCZOS)
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
        bg = bg.filter(ImageFilter.GaussianBlur(60)).point(lambda v: int(v * 0.6))
        fg_scale = min(W / img.width, H / img.height)
        fg = img.resize((round(img.width * fg_scale), round(img.height * fg_scale)), Image.LANCZOS)
        bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        img = bg
    if overlay:
        if overlay.get("custom_html"):
            img = draw_custom_html(img, overlay["custom_html"])
        elif overlay.get("content_image_path"):
            img = draw_content_image(img, overlay["content_image_path"])
        elif overlay.get("show_title_card") and overlay.get("title_text"):
            img = draw_beat_heading(img, overlay["title_text"])
        if overlay.get("code_text"):
            img = draw_code_block(img, overlay["code_text"], overlay.get("code_theme") or "dark",
                                   overlay.get("code_font_size") or 44)
    if warm:
        img = warm_grade(img)
    img.save(dst, "JPEG", quality=94)


def _zoompan(motion: str, frames: int, strength: float) -> str:
    n = max(frames - 1, 1)
    e = f"(on/{n})*(on/{n})*(3-2*(on/{n}))"          # smoothstep easing
    a = strength
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    z, x, y = {
        "zoom_in": (f"1+{a}*{e}", cx, cy),
        "zoom_out": (f"1+{a}*(1-{e})", cx, cy),
        "zoom_in_top": (f"1+{a}*{e}", cx, "(ih-ih/zoom)*0.25"),
        "pan_right": (f"{1+a}", f"(iw-iw/zoom)*{e}", cy),
        "pan_left": (f"{1+a}", f"(iw-iw/zoom)*(1-{e})", cy),
        "pan_up": (f"{1+a}", cx, f"(ih-ih/zoom)*(1-{e})"),
        "pan_down": (f"{1+a}", cx, f"(ih-ih/zoom)*{e}"),
    }[motion]
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={OUT_W}x{OUT_H}:fps={FPS}"


def _run(cmd: list[str], cwd: Path | None = None, total: float | None = None,
         on_progress: Callable[[float], None] | None = None) -> None:
    if total and on_progress:
        cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_tail: list[str] = []

    def drain_err():
        for line in p.stderr:
            err_tail.append(line)
            del err_tail[:-40]
    import threading
    t = threading.Thread(target=drain_err, daemon=True)
    t.start()
    for line in p.stdout:
        m = re.match(r"out_time_us=(\d+)", line)
        if m and total and on_progress:
            on_progress(min(1.0, int(m.group(1)) / 1e6 / total))
    p.wait()
    t.join(timeout=2)
    if p.returncode != 0:
        raise RenderError("ffmpeg failed:\n" + "".join(err_tail[-15:]))


def plan_timeline(durations: list[float], opts: dict) -> dict:
    T = 0.0 if opts["transition"] == "cut" else float(opts["transition_duration"])
    pause = float(opts["scene_pause"])
    starts, leads, lengths = [], [], []
    s = 0.0
    for i, a in enumerate(durations):
        lead = 0.3 if i == 0 else T + 0.05
        tail = 1.0 if i == len(durations) - 1 else T + pause
        L = lead + a + tail
        starts.append(s); leads.append(lead); lengths.append(L)
        s += L - T
    total = starts[-1] + lengths[-1]
    return {"T": T, "starts": starts, "leads": leads, "lengths": lengths, "total": total}


def _escape_drawtext_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def _countdown_filter(lead: float) -> str:
    """3-2-1 drawtext overlays, timed to start exactly when the wait beat's (silent) audio starts."""
    font = _escape_drawtext_path(FONTS / "Mukta_800ExtraBold.ttf")
    out = ""
    for n, offset in ((3, 0.0), (2, 1.0), (1, 2.0)):
        lo, hi = lead + offset, lead + offset + 1
        out += (f",drawtext=fontfile='{font}':text='{n}':fontsize=420:fontcolor=white:"
                f"x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t\\,{lo:.3f}\\,{hi:.3f})'")
    return out


def render(scenes: list[dict], lang: str, opts: dict, work: Path, out: Path,
           progress: Callable[[float, str], None], seed: int = 0) -> dict:
    """scenes: [{image, audio, duration, words}] in order."""
    opts = {**DEFAULT_OPTIONS, **(opts or {})}
    if not scenes:
        raise RenderError("No scenes")
    work.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    tl = plan_timeline([s["duration"] for s in scenes], opts)
    strength = {"gentle": 0.1, "normal": 0.18, "strong": 0.28}.get(opts["motion"], 0.18)

    background_mode = opts.get("visual_mode") == "background"
    motions: list[str] = []

    if background_mode:
        progress(0.1, "Preparing background")
        bg_path = ensure_background(opts.get("background") or "")
    else:
        # 1) image prep + Ken Burns clip per scene (parallel)
        progress(0.02, "Preparing images")
        last = None
        for _ in scenes:
            m = rng.choice([x for x in MOTIONS if x != last])
            motions.append(m); last = m

        def make_clip(i: int) -> Path:
            sc = scenes[i]
            img = work / f"img_{i:02d}.jpg"
            overlay = {"show_title_card": sc.get("show_title_card", False), "title_text": sc.get("narration", ""),
                       "code_text": sc.get("code_text", ""),
                       "content_image_path": sc.get("content_image_path", ""),
                       "custom_html": sc.get("custom_html", ""),
                       "code_theme": sc.get("code_theme", ""),
                       "code_font_size": sc.get("code_font_size", 0)} if sc.get("beat_type") else None
            fit_vertical(Path(sc["image"]), img, bool(opts.get("warm")), overlay)
            frames = round(tl["lengths"][i] * FPS)
            clip = work / f"clip_{i:02d}.mp4"
            vf = _zoompan(motions[i], frames, strength) + ",format=yuv420p"
            if sc.get("beat_type") == "wait":
                vf += _countdown_filter(tl["leads"][i])
            _run([FFMPEG, "-y", "-v", "error", "-i", str(img), "-vf", vf,
                  "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(clip)])
            return clip

        done = 0
        clips: list[Path] = [None] * len(scenes)  # type: ignore
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(make_clip, i): i for i in range(len(scenes))}
            for f in futs:
                clips[futs[f]] = f.result()
                done += 1
                progress(0.02 + 0.5 * done / len(scenes), f"Animated scene {done}/{len(scenes)}")

    # 2) captions
    words = []
    for i, s in enumerate(scenes):
        off = tl["starts"][i] + tl["leads"][i]
        for w in s["words"]:
            words.append({"w": w["w"], "start": off + w["start"], "end": off + w["end"]})
    title = (opts.get("title_text") or "").strip() if opts.get("show_title") else ""
    (work / "captions.ass").write_text(build_ass(words, lang, opts["captions"], opts["highlight"], title,
                                                 position=opts["caption_position"]), encoding="utf-8")
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in FONTS.glob("*.ttf"):
        shutil.copy(f, fonts / f.name)

    # 3) final composition
    n = len(scenes)
    n_vid = 1 if background_mode else n
    inputs: list[str] = []
    if background_mode:
        inputs += ["-stream_loop", "-1", "-i", str(bg_path)]
    else:
        for c in clips:
            inputs += ["-i", c.name]
    for s in scenes:
        inputs += ["-i", str(Path(s["audio"]).resolve())]
    music = opts.get("music") or ""
    music_path = MUSIC / music if music else None
    if music_path and music_path.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music_path.resolve())]
    else:
        music_path = None

    T, total = tl["T"], tl["total"]
    fc = []
    if background_mode:
        fc.append(f"[0:v]trim=duration={total:.3f},setpts=PTS-STARTPTS[bgv]")
        vlast = "[bgv]"
    elif n == 1:
        vlast = "[0:v]"
    else:
        vlast = "[0:v]"
        for i in range(1, n):
            tr = opts["transition"]
            if tr in ("auto", "cut"):
                tr = rng.choice(TRANSITIONS) if tr == "auto" else "fade"
            dur = T if T > 0 else 0.04
            fc.append(f"{vlast}[{i}:v]xfade=transition={tr}:duration={dur}:offset={tl['starts'][i]:.3f}[x{i}]")
            vlast = f"[x{i}]"
    post = []
    if opts.get("vignette"):
        post.append("vignette=angle=PI/5")
    if opts.get("grain"):
        post.append("noise=alls=5:allf=t")
    post.append("ass=captions.ass:fontsdir=fonts")
    post.append("format=yuv420p")
    fc.append(f"{vlast}{','.join(post)}[vout]")

    for i in range(n):
        ms = int(round((tl["starts"][i] + tl["leads"][i]) * 1000))
        fc.append(f"[{n_vid + i}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}:all=1[a{i}]")
    amix_in = "".join(f"[a{i}]" for i in range(n))
    fc.append(f"{amix_in}amix=inputs={n}:normalize=0:dropout_transition=0,apad=whole_dur={total:.3f},"
              f"atrim=0:{total:.3f}[narr]")
    if music_path:
        m = n_vid + n
        vol = float(opts.get("music_volume", 0.16))
        fc.append(f"[{m}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},volume={vol:.3f},"
                  f"afade=t=in:d=1.0,afade=t=out:st={max(0, total - 2.5):.3f}:d=2.5[mus]")
        fc.append("[narr]asplit=2[n1][n2]")
        fc.append("[mus][n2]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=450[duck]")
        fc.append("[n1][duck]amix=inputs=2:normalize=0[mix]")
        alast = "[mix]"
    else:
        alast = "[narr]"
    fc.append(f"{alast}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    (work / "filter.txt").write_text(";\n".join(fc))
    cmd = [FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "[aout]", "-r", str(FPS),
           "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.3f}",
           str(out.resolve())]
    progress(0.55, "Compositing video")
    _run(cmd, cwd=work, total=total, on_progress=lambda f: progress(0.55 + 0.44 * f, "Compositing video"))
    progress(1.0, "Done")
    return {"duration": total, "timeline": tl, "motions": motions}
