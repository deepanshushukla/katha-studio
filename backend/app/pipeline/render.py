"""FFmpeg composition: Ken Burns clips -> crossfades -> grade -> captions -> narration + ducked music."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pygments import lex
from pygments.lexers import JavascriptLexer
from pygments.token import Token

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


MONO_FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
]
_font_cache: dict[tuple[str, int], "ImageFont.FreeTypeFont"] = {}


def _mono_font(size: int):
    key = ("mono", size)
    if key not in _font_cache:
        for path in MONO_FONT_CANDIDATES:
            if Path(path).exists():
                _font_cache[key] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _heading_font(size: int):
    key = ("heading", size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(FONTS / "Mukta_800ExtraBold.ttf"), size)
    return _font_cache[key]


TOKEN_COLORS = {
    Token.Keyword: (198, 120, 221),
    Token.Name.Function: (97, 175, 239),
    Token.Literal.String: (152, 195, 121),
    Token.Literal.Number: (209, 154, 102),
    Token.Comment: (92, 99, 112),
    Token.Operator: (224, 108, 117),
}


def _token_color(tok) -> tuple[int, int, int]:
    for t, c in TOKEN_COLORS.items():
        if tok in t:
            return c
    return (220, 223, 228)


def _wrap_code_lines(code: str, max_chars: int) -> list[str]:
    out = []
    for line in code.splitlines() or [""]:
        while len(line) > max_chars:
            out.append(line[:max_chars])
            line = line[max_chars:]
        out.append(line)
    return out


def draw_code_block(img: Image.Image, code: str) -> Image.Image:
    """Paste a syntax-highlighted monospace code block, centred, onto img. No-op if code is blank."""
    if not code.strip():
        return img
    size = 44 * WORK_SCALE
    font = _mono_font(size)
    max_chars = 34
    lines = _wrap_code_lines(code, max_chars)
    line_h = int(size * 1.5)
    pad = 48 * WORK_SCALE
    char_w = font.getlength("M") or size * 0.6
    box_w = min(img.width - 2 * pad, int(max_chars * char_w) + 2 * pad)
    box_h = len(lines) * line_h + 2 * pad
    box = Image.new("RGBA", (box_w, box_h), (18, 20, 26, 235))
    d = ImageDraw.Draw(box)
    d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=24 * WORK_SCALE, outline=(70, 75, 90, 255), width=3)
    y = pad
    for line in lines:
        x = pad
        for tok, val in lex(line + "\n", JavascriptLexer()):
            val = val.rstrip("\n")
            if not val:
                continue
            d.text((x, y), val, font=font, fill=_token_color(tok))
            x += font.getlength(val)
        y += line_h
    img = img.convert("RGBA")
    pos = ((img.width - box_w) // 2, (img.height - box_h) // 2 + int(120 * WORK_SCALE))
    img.alpha_composite(box, pos)
    return img.convert("RGB")


def draw_beat_heading(img: Image.Image, text: str) -> Image.Image:
    """Paste the beat's question/answer text as a multi-line heading near the top. No-op if text is blank."""
    if not text.strip():
        return img
    size = 54 * WORK_SCALE
    font = _heading_font(size)
    pad = 60 * WORK_SCALE
    max_width = img.width - 2 * pad
    lines: list[str] = []
    for raw_line in text.strip().splitlines():
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            cand = cur + " " + w
            if font.getlength(cand) <= max_width:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    line_h = int(size * 1.35)
    box_h = len(lines) * line_h + 2 * pad
    box = Image.new("RGBA", (img.width, box_h), (10, 8, 20, 190))
    d = ImageDraw.Draw(box)
    y = pad
    for line in lines:
        w = font.getlength(line)
        d.text(((img.width - w) / 2, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img = img.convert("RGBA")
    img.alpha_composite(box, (0, int(90 * WORK_SCALE)))
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
        if overlay.get("show_title_card") and overlay.get("title_text"):
            img = draw_beat_heading(img, overlay["title_text"])
        if overlay.get("code_text"):
            img = draw_code_block(img, overlay["code_text"])
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
            img = work / f"img_{i:02d}.jpg"
            fit_vertical(Path(scenes[i]["image"]), img, bool(opts.get("warm")))
            frames = round(tl["lengths"][i] * FPS)
            clip = work / f"clip_{i:02d}.mp4"
            _run([FFMPEG, "-y", "-v", "error", "-i", str(img), "-vf",
                  _zoompan(motions[i], frames, strength) + ",format=yuv420p",
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
