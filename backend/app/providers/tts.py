"""Text-to-speech: Microsoft Edge neural voices (free, online), offline beeps."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import struct
import subprocess
import wave
from pathlib import Path

from ..config import FFMPEG, FFPROBE


class TTSError(RuntimeError):
    pass


EDGE_VOICES = [
    {"id": "hi-IN-SwaraNeural", "name": "Swara", "gender": "Female", "lang": "hi", "note": "Warm, clear Hindi"},
    {"id": "hi-IN-MadhurNeural", "name": "Madhur", "gender": "Male", "lang": "hi", "note": "Calm Hindi storyteller"},
    {"id": "en-IN-NeerjaExpressiveNeural", "name": "Neerja (expressive)", "gender": "Female", "lang": "en", "note": "Indian English, lively"},
    {"id": "en-IN-NeerjaNeural", "name": "Neerja", "gender": "Female", "lang": "en", "note": "Indian English"},
    {"id": "en-IN-PrabhatNeural", "name": "Prabhat", "gender": "Male", "lang": "en", "note": "Indian English, deep"},
]

OFFLINE_VOICES = [{"id": "test-beeps", "name": "Test tone (offline)", "gender": "-", "lang": "*",
                   "note": "Beeps per word — only for trying the app without internet"}]


def voices(lang: str) -> dict:
    return {
        "edge": [v for v in EDGE_VOICES if v["lang"] == lang],
        "offline": OFFLINE_VOICES,
    }


# ---------- helpers ----------
def duration_of(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def apply_rate_pitch(src: Path, dst: Path, rate: int, pitch: int) -> None:
    """Post-process self-recorded/voice-changed audio. rate: percent speed, -30..30.
    pitch: ~2 units per semitone."""
    af = []
    if pitch:
        factor = 2 ** (pitch / 2 / 12)
        af.append(f"asetrate=48000*{factor:.4f},aresample=48000,atempo={1 / factor:.4f}")
    if rate:
        af.append(f"atempo={max(0.5, min(2.0, 1 + rate / 100)):.4f}")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", ",".join(af), "-ac", "1", "-ar", "48000", str(dst)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise TTSError(f"ffmpeg rate/pitch adjust failed: {p.stderr[-300:]}")


def to_wav(src: Path, dst: Path) -> None:
    p = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "48000", str(dst)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise TTSError(f"ffmpeg conversion failed: {p.stderr[-300:]}")


def make_silence(out_wav: Path, seconds: float, sr: int = 48000) -> None:
    """A silent mono WAV of the given length — used for the code-quiz "wait" beat, which has no narration."""
    n = int(sr * seconds)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)


def speech_bounds(path: Path, total: float) -> tuple[float, float]:
    """First/last non-silent moments, via ffmpeg silencedetect."""
    p = subprocess.run([FFMPEG, "-v", "info", "-i", str(path), "-af", "silencedetect=n=-35dB:d=0.12", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    begin, finish = 0.0, total
    if starts and starts[0] < 0.05 and ends:
        begin = ends[0]
    if starts and starts[-1] > begin and (len(ends) < len(starts) or ends[-1] >= total - 0.05):
        finish = starts[-1]
    return begin, max(finish, begin + 0.1)


def estimate_words(text: str, begin: float, end: float) -> list[dict]:
    """Spread words across the spoken span proportionally to their length (+ pauses at punctuation)."""
    toks = text.split()
    if not toks:
        return []
    weights = [len(re.sub(r"[^\w]", "", t)) + 2 + (3 if re.search(r"[,;:—–]$", t) else 0)
               + (5 if re.search(r"[.!?।]$", t) else 0) for t in toks]
    unit = (end - begin) / sum(weights)
    out, t = [], begin
    for tok, w in zip(toks, weights):
        out.append({"w": tok, "start": round(t, 3), "end": round(t + unit * (w - 1), 3)})
        t += unit * w
    return out


def _align_to_text(text: str, boundaries: list[dict]) -> list[dict]:
    """Map TTS word boundaries back onto the original tokens (keeps punctuation for captions)."""
    toks = text.split()
    if not boundaries:
        return []
    out, bi = [], 0
    for tok in toks:
        core = re.sub(r"[^\w]", "", tok)
        if not core:  # standalone punctuation ("—", "...") – glue to previous word
            if out:
                out[-1]["w"] += " " + tok
            continue
        if bi < len(boundaries):
            b = boundaries[bi]
            out.append({"w": tok, "start": b["start"], "end": b["end"]})
            # a token may be split into several boundaries (e.g. hyphenated) – swallow them
            joined = re.sub(r"[^\w]", "", b["text"])
            while bi + 1 < len(boundaries) and len(joined) < len(core):
                bi += 1
                joined += re.sub(r"[^\w]", "", boundaries[bi]["text"])
                out[-1]["end"] = boundaries[bi]["end"]
            bi += 1
        else:
            last = out[-1]["end"] if out else 0
            out.append({"w": tok, "start": last, "end": last + 0.2})
    return out


# ---------- Edge ----------
def _edge(text: str, voice: str, rate: int, pitch: int, out_wav: Path) -> list[dict]:
    import edge_tts

    async def run(mp3: Path) -> list[dict]:
        comm = edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz", boundary="WordBoundary")
        bounds = []
        with open(mp3, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    st = chunk["offset"] / 1e7
                    bounds.append({"text": chunk["text"], "start": round(st, 3), "end": round(st + chunk["duration"] / 1e7, 3)})
        return bounds

    mp3 = out_wav.with_suffix(".mp3")
    last = None
    for _ in range(3):
        try:
            bounds = asyncio.run(run(mp3))
            if mp3.stat().st_size < 1000:
                raise TTSError("empty audio")
            to_wav(mp3, out_wav)
            mp3.unlink(missing_ok=True)
            return _align_to_text(text, bounds)
        except Exception as e:  # noqa: BLE001
            last = e
    raise TTSError(f"Edge TTS failed: {last}")


# ---------- offline ----------
def _offline(text: str, voice: str, rate: int, pitch: int, out_wav: Path) -> list[dict]:
    sr, words, frames, t = 48000, [], bytearray(), 0.25
    speed = 1 + rate / 100
    frames += b"\0\0" * int(sr * 0.25)
    for i, tok in enumerate(text.split()):
        d = (0.12 + 0.045 * len(re.sub(r"[^\w]", "", tok))) / speed
        f = 300 + 40 * (i % 4) + pitch * 4
        n = int(sr * d)
        for k in range(n):
            env = math.sin(math.pi * k / n)
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * f * k / sr)))
        words.append({"w": tok, "start": round(t, 3), "end": round(t + d, 3)})
        gap = (0.08 + (0.3 if re.search(r"[.!?।,]$", tok) else 0)) / speed
        frames += b"\0\0" * int(sr * gap)
        t += d + gap
    frames += b"\0\0" * int(sr * 0.2)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(frames))
    return words


PROVIDERS = {"edge": _edge, "offline": _offline}


def synth(text: str, provider: str, voice: str, rate: int, pitch: int, out_wav: Path) -> tuple[float, list[dict], str]:
    """Returns (duration seconds, word timings, provider used)."""
    forced = set(filter(None, os.environ.get("KATHA_FORCE_FAIL", "").split(",")))  # test hook
    try:
        if provider in forced:
            raise TTSError("forced failure (test)")
        words = PROVIDERS[provider](text, voice, rate, pitch, out_wav)
        dur = duration_of(out_wav)
        if not words:
            b, e = speech_bounds(out_wav, dur)
            words = estimate_words(text, b, e)
        return dur, words, provider
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"{provider}: {e}")
