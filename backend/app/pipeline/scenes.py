"""Story -> scene script (+ character bible), image prompt assembly, and upload metadata."""
from __future__ import annotations

import re

from ..providers.llm import generate_json

STYLES = {
    "cinematic": {"label": "Cinematic realistic",
                  "prompt": "epic cinematic film still, photorealistic, dramatic volumetric lighting, rich colour grading, "
                            "35mm, shallow depth of field, ultra detailed, divine atmosphere"},
    "miniature": {"label": "Indian miniature painting",
                  "prompt": "traditional Indian miniature painting, Rajput and Pahari style, gold leaf details, flat perspective, "
                            "intricate borders feel, mineral pigments, ornate patterns"},
    "comic": {"label": "Classic Indian comic",
              "prompt": "vintage Indian mythological comic book illustration, bold ink outlines, flat vibrant colours, "
                        "halftone shading, heroic poses"},
    "anime": {"label": "Anime / illustrated",
              "prompt": "high quality anime key visual, cel shading, luminous colours, detailed background, dynamic composition"},
    "oil": {"label": "Raja Ravi Varma oil painting",
            "prompt": "classical Indian oil painting in the style of Raja Ravi Varma, realistic figures, soft glowing light, "
                      "rich textiles and jewellery, canvas texture"},
    "3d": {"label": "3D animated film",
           "prompt": "stylised 3D animated movie render, Pixar-like lighting, soft global illumination, expressive characters"},
    "custom": {"label": "Custom (write your own)", "prompt": ""},
}

LANG_NAME = {"hi": "Hindi (Devanagari script, simple spoken Hindi, not overly Sanskritised)", "en": "English (Indian storytelling tone)"}
WORDS_PER_SEC = {"hi": 2.3, "en": 2.5}


def style_prompt(style_key: str, custom: str) -> str:
    if style_key == "custom" or not STYLES.get(style_key, {}).get("prompt"):
        return custom.strip() or STYLES["cinematic"]["prompt"]
    return STYLES[style_key]["prompt"] + (f", {custom.strip()}" if custom.strip() else "")


SYSTEM = """You are a master Indian mythological storyteller and a short-form video director.
You turn a story into a vertical video script for YouTube Shorts / Instagram Reels.
Always answer with a single JSON object and nothing else."""


def build_user_prompt(story: str, lang: str, seconds: int) -> str:
    n_scenes = max(5, min(14, round(seconds / 6)))
    words = int(seconds * WORDS_PER_SEC[lang])
    return f"""STORY:
\"\"\"{story.strip()}\"\"\"

Create a script with exactly {n_scenes} scenes. The whole narration must be about {words} words in total
(roughly {seconds} seconds when read aloud) written in {LANG_NAME[lang]}.

Rules:
- Scene 1 opens with a strong hook in the first sentence (a question or a dramatic moment) so viewers keep watching.
- Stay faithful to the story; keep names respectful and correct. Last scene ends with a short moral or a memorable line.
- Narration is spoken text only: no stage directions, no emojis, no hashtags.
- image_prompt is ALWAYS in English: one vivid, concrete visual moment (subject, action, setting, camera angle, lighting,
  mood) composed for a tall 9:16 frame. Mention characters by the exact names used in "characters". No text in image.
- caption: 2-5 punchy words in {LANG_NAME[lang].split(' (')[0]} (optional on-screen label).
- characters: every recurring character with a fixed visual description in English (age, body, skin, hair, face,
  clothing, ornaments, weapon/attribute, colours) following traditional iconography, so every image looks consistent.

JSON shape:
{{"title": "...", "characters": [{{"name": "...", "description": "..."}}],
  "scenes": [{{"narration": "...", "image_prompt": "...", "caption": "..."}}]}}"""


def _offline_script(story: str, lang: str, seconds: int) -> dict:
    """No-LLM fallback: split the story into sentences and group them into scenes."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", story.strip()) if s.strip()]
    n = max(1, min(len(sents), max(5, min(14, round(seconds / 6)))))
    size = -(-len(sents) // n)
    groups = [" ".join(sents[i:i + size]) for i in range(0, len(sents), size)]
    return {
        "title": (sents[0][:60] if sents else "Story"),
        "characters": [],
        "scenes": [{"narration": g, "image_prompt": g if lang == "en" else f"Scene {i+1} of an Indian mythological story",
                    "caption": ""} for i, g in enumerate(groups)],
    }


def make_script(story: str, lang: str, seconds: int) -> tuple[dict, str, list[str]]:
    data, provider, errors = generate_json(SYSTEM, build_user_prompt(story, lang, seconds))
    if not data or not data.get("scenes"):
        return _offline_script(story, lang, seconds), "offline", errors
    scenes = []
    for sc in data["scenes"]:
        if isinstance(sc, dict) and str(sc.get("narration", "")).strip():
            scenes.append({"narration": str(sc["narration"]).strip(),
                           "image_prompt": str(sc.get("image_prompt", "")).strip(),
                           "caption": str(sc.get("caption", "")).strip()})
    chars = [{"name": str(c.get("name", "")).strip(), "description": str(c.get("description", "")).strip()}
             for c in data.get("characters", []) if isinstance(c, dict) and c.get("name")]
    return {"title": str(data.get("title", "")).strip(), "characters": chars, "scenes": scenes}, provider, errors


TITLES = {"lord", "goddess", "god", "king", "queen", "sage", "the", "demon", "prince", "princess", "rishi",
          "maharishi", "devi", "shri", "sri", "young", "old"}


def _mentions(name: str, text: str) -> bool:
    toks = [w for w in re.findall(r"\w+", name) if len(w) >= 3 and w.lower() not in TITLES] or name.split()
    return any(re.search(rf"\b{re.escape(t)}", text, re.I) for t in toks)


def full_image_prompt(scene_prompt: str, characters: list, style: str) -> str:
    present = [c for c in characters if c.get("name") and _mentions(c["name"], scene_prompt)]
    char_txt = " ".join(f"{c['name']}: {c['description']}." for c in present)
    parts = [style, scene_prompt]
    if char_txt:
        parts.append("Characters — " + char_txt)
    parts.append("vertical 9:16 composition, subject centred with headroom, highly detailed, masterpiece, "
                 "no text, no letters, no watermark, no logo")
    return ". ".join(p.strip().rstrip(".") for p in parts if p.strip())


def make_metadata(title: str, story: str, narration: str, lang: str) -> dict:
    user = f"""Write upload metadata for a YouTube Short / Instagram Reel of this Indian mythological story.
Language: {LANG_NAME[lang]}. Working title: {title}
Narration: \"\"\"{narration[:3000]}\"\"\"
Return JSON: {{"title": "catchy title under 70 characters with 1 fitting emoji",
"description": "2-3 engaging sentences + a call to follow for more stories",
"hashtags": ["10-15 relevant hashtags without spaces, mix of Hindi/English reach tags like #shorts #mythology"]}}"""
    data, _, _ = generate_json("You write viral but honest social media metadata. Answer only JSON.", user)
    if not data:
        base = ["#shorts", "#reels", "#mythology", "#indianmythology", "#hindumythology", "#story", "#sanatandharma",
                "#pauranikkatha", "#kahani", "#storytime"]
        return {"title": title[:70], "description": narration[:200] + "…\nFollow for more stories!", "hashtags": base}
    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = ["#" + t.lstrip("#").replace(" ", "") for t in tags if str(t).strip()]
    return {"title": str(data.get("title", title))[:100], "description": str(data.get("description", "")), "hashtags": tags}
