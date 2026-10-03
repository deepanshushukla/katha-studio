"""Word-timed ASS subtitles (karaoke highlight) + title card."""
from __future__ import annotations

import re

FONT = "Mukta ExtraBold"   # bundled in backend/fonts, covers Devanagari + Latin.
# NB: keep Spacing=0 in styles – letter spacing disables libass text shaping (breaks Devanagari).

CAPTION_STYLES = {
    "karaoke": "Word-by-word highlight",
    "simple": "Plain lines",
    "off": "No captions",
}

HIGHLIGHT = {"gold": "&H0030D5FF&", "saffron": "&H001E8CFF&", "cyan": "&H00FFE14D&", "white": "&H00FFFFFF&"}


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(s: str) -> str:
    return re.sub(r"[{}\\]", "", s).replace("\n", " ")


def chunk_words(words: list[dict], lang: str) -> list[list[dict]]:
    max_chars = 20 if lang == "hi" else 22
    groups, cur = [], []
    for w in words:
        cand = " ".join(x["w"] for x in cur + [w])
        if cur and (len(cand) > max_chars or len(cur) >= 4):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?।]$", w["w"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], lang: str, style: str = "karaoke", highlight: str = "gold",
              title: str = "", title_dur: float = 2.8, position: str = "lower") -> str:
    margin_v = {"lower": 560, "middle": 860, "bottom": 330}.get(position, 560)
    hl = HIGHLIGHT.get(highlight, HIGHLIGHT["gold"])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,7,3,2,90,90,{margin_v},1
Style: Title,{FONT},112,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,0,0,0,0,100,100,0,0,1,8,4,8,70,70,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if title.strip():
        ev.append(f"Dialogue: 1,{_ts(0.15)},{_ts(title_dur)},Title,,0,0,0,,"
                  f"{{\\fad(250,400)\\c{hl}\\t(0,300,\\fscx104\\fscy104)}}{_clean(title)}")
    if style != "off" and words:
        groups = chunk_words(words, lang)
        for gi, g in enumerate(groups):
            g_start = g[0]["start"]
            nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
            g_end = g[-1]["end"] + 0.25
            if nxt is not None and nxt - g[-1]["end"] < 0.6:
                g_end = nxt
            elif nxt is not None:
                g_end = min(g_end, nxt)
            texts = [_clean(w["w"]) for w in g]
            if style == "simple":
                ev.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{{\\fad(80,60)}}{' '.join(texts)}")
                continue
            for wi, w in enumerate(g):
                s = g_start if wi == 0 else w["start"]
                e = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
                if e <= s:
                    continue
                parts = []
                for k, t in enumerate(texts):
                    if k == wi:
                        parts.append(f"{{\\c{hl}\\fscx112\\fscy112}}{t}{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                    else:
                        parts.append(t)
                intro = "{\\fad(70,0)}" if wi == 0 else ""
                ev.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{intro}{' '.join(parts)}")
    return head + "\n".join(ev) + "\n"
