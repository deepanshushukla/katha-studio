"""End-to-end smoke test using the offline providers (no internet needed).

Run from katha-studio/:  backend/.venv/bin/python backend/tests/smoke_test.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

os.environ.setdefault("KATHA_DATA", tempfile.mkdtemp(prefix="katha_test_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import FFMPEG, FFPROBE, PROJECTS, TRADEMARK_IMAGE  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Scene, session  # noqa: E402
from sqlmodel import select  # noqa: E402

c = TestClient(app)

STORIES = {
    "hi": "एक बार माता पार्वती स्नान करने गईं। उन्होंने अपने उबटन से एक सुंदर बालक बनाया और उसमें प्राण डाल दिए। "
          "उन्होंने उसे द्वार पर पहरा देने को कहा। तभी भगवान शिव आए, पर बालक ने उन्हें रोक दिया। "
          "क्रोध में शिव ने बालक का सिर काट दिया। पार्वती के दुःख को देखकर शिव ने बालक को हाथी का सिर लगाकर जीवित किया। "
          "वही बालक गणेश कहलाए, जिनकी हर शुभ काम से पहले पूजा होती है।",
    "en": "Long ago, Goddess Parvati created a boy from sandalwood paste and breathed life into him. "
          "She asked him to guard her door. When Lord Shiva arrived, the boy refused to let him in. "
          "In anger, Shiva struck off the boy's head. Seeing Parvati's grief, Shiva restored the boy with an elephant's head. "
          "He became Ganesha, the remover of obstacles, worshipped before every new beginning.",
}


def wait(job):
    while job["status"] == "running":
        time.sleep(0.3)
        job = c.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    return job


def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "stream=codec_name,width,height,sample_rate:format=duration", "-of", "json", path],
                         capture_output=True, text=True).stdout
    return json.loads(out)


def run(lang, music=None, fail_images=False):
    c.put("/api/settings", json={"llm_provider": "offline", "image_provider": "offline", "tts_provider": "offline"})
    pid = c.post("/api/projects", json={"story": STORIES[lang], "language": lang, "style_key": "miniature",
                                        "target_seconds": 30}).json()["id"]
    c.patch(f"/api/projects/{pid}", json={"tts_provider": "offline", "voice": "test-beeps"})
    wait(c.post(f"/api/projects/{pid}/script").json())
    p = c.get(f"/api/projects/{pid}").json()
    assert len(p["scenes"]) >= 3, p["scenes"]
    # edit one scene like a user would
    scenes = [{"id": s["id"], "narration": s["narration"], "image_prompt": s["image_prompt"], "caption": s["caption"]}
              for s in p["scenes"]]
    scenes[0]["image_prompt"] = "Goddess Parvati in a palace garden at dawn"
    c.put(f"/api/projects/{pid}/scenes", json=scenes)
    wait(c.post(f"/api/projects/{pid}/images", json={"variants": 2}).json())
    p = c.get(f"/api/projects/{pid}").json()
    for s in p["scenes"]:
        assert len(s["images"]) == 2
        c.post(f"/api/scenes/{s['id']}/approve", json={"image_id": s["images"][1]["id"]})
    job = wait(c.post(f"/api/projects/{pid}/narration", json={}).json())
    assert c.post(f"/api/projects/{pid}/narration", json={}).json()  # idempotent
    wait(c.post(f"/api/projects/{pid}/metadata").json())
    opts = {"music": music or "", "captions": "karaoke"}
    job = wait(c.post(f"/api/projects/{pid}/render", json=opts).json())
    p = c.get(f"/api/projects/{pid}").json()
    mp4 = PROJECTS / p["last_render_url"].split("/files/")[1].split("?")[0]
    info = probe(str(mp4))
    v = next(s for s in info["streams"] if s["codec_name"] == "h264")
    a = next(s for s in info["streams"] if s["codec_name"] == "aac")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert a["sample_rate"] == "48000"
    dur = float(info["format"]["duration"])
    assert abs(dur - job["result"]["duration"]) < 0.15, (dur, job["result"])
    print(f"[{lang}] OK  scenes={len(p['scenes'])}  duration={dur:.2f}s  file={mp4}")
    print("   metadata:", p["metadata"].get("title"), " ".join(p["metadata"].get("hashtags", [])[:5]))
    return pid, mp4


def test_self_recording_voice_change():
    """record-audio stores the pristine take; repeated change-voice applies must always
    re-process from that take, never from a previous apply's output."""
    c.put("/api/settings", json={"llm_provider": "offline", "image_provider": "offline", "tts_provider": "offline"})
    pid = c.post("/api/projects", json={"story": STORIES["en"], "language": "en", "style_key": "miniature",
                                        "target_seconds": 30}).json()["id"]
    wait(c.post(f"/api/projects/{pid}/script").json())
    sid = c.get(f"/api/projects/{pid}").json()["scenes"][0]["id"]

    rec = Path(tempfile.mkdtemp()) / "take.wav"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5",
                    "-ar", "48000", "-ac", "1", str(rec)], check=True, capture_output=True)
    with open(rec, "rb") as f:
        job = wait(c.post(f"/api/scenes/{sid}/record-audio", files={"file": ("take.wav", f, "audio/wav")}).json())
    sc_view = next(s for s in job["result"]["scenes"] if s["id"] == sid)
    assert sc_view["audio_self"] is True, sc_view

    with session() as s:
        original = s.get(Scene, sid).audio_original_path
    assert original and Path(original).exists()

    wait(c.post(f"/api/scenes/{sid}/change-voice", data={"pitch": "2"}).json())
    with session() as s:
        row = s.get(Scene, sid)
    assert row.audio_original_path == original and Path(original).exists(), "1st apply must not touch the original"
    path_after_1 = row.audio_path

    wait(c.post(f"/api/scenes/{sid}/change-voice", data={"pitch": "5"}).json())
    with session() as s:
        row = s.get(Scene, sid)
    assert row.audio_original_path == original and Path(original).exists(), "2nd apply must not touch the original"
    assert row.audio_path != path_after_1, "2nd apply should produce a fresh output, not chain onto the 1st"
    print("[voice] OK  original preserved across repeated change-voice applies")


def test_code_quiz_flow():
    """A code-quiz project is created from raw question/code/answer text (no LLM), gets exactly
    3 beat scenes (question/wait/answer), and renders end to end with the wait beat silent for 3s."""
    c.put("/api/settings", json={"llm_provider": "offline", "image_provider": "offline", "tts_provider": "offline"})

    buf = io.BytesIO()
    Image.new("RGB", (600, 1000), (10, 20, 30)).save(buf, "PNG")
    assert c.post("/api/settings/trademark-image", files={"file": ("bg.png", buf.getvalue(), "image/png")}).status_code == 200

    pid = c.post("/api/projects", json={"content_type": "code_quiz", "language": "en"}).json()["id"]
    question = "What does this log?\nA) 5\nB) 10\nC) 15\nD) 20"
    code = "function counter() {\n  let x = 5\n  return x\n}"
    answer = "The answer is B) 10, because the closure captures x by reference."

    qbuf = io.BytesIO(); Image.new("RGB", (50, 50), (5, 6, 7)).save(qbuf, "PNG")
    job = wait(c.post(f"/api/projects/{pid}/code-quiz",
                       data={"question": question, "code": code, "answer": answer},
                       files={"question_image": ("q.png", qbuf.getvalue(), "image/png")}).json())
    scenes = job["result"]["scenes"]
    assert [s["beat_type"] for s in scenes] == ["question", "wait", "answer"], scenes
    assert scenes[0]["narration"] == question and scenes[0]["code_text"] == code
    assert scenes[0]["content_image_url"], "question_image override should be stored"
    assert scenes[1]["narration"] == "" and abs(scenes[1]["audio_duration"] - 3.0) < 0.01
    assert scenes[2]["narration"] == answer and scenes[2]["code_text"] == code
    assert all(s["show_title_card"] for s in scenes)

    with session() as s:
        old_question_image = Path(s.get(Scene, scenes[0]["id"]).content_image_path)
    assert old_question_image.exists()

    job = wait(c.post(f"/api/projects/{pid}/render", json={}).json())
    p = c.get(f"/api/projects/{pid}").json()
    mp4 = PROJECTS / p["last_render_url"].split("/files/")[1].split("?")[0]
    info = probe(str(mp4))
    dur = float(info["format"]["duration"])
    assert abs(dur - job["result"]["duration"]) < 0.15, (dur, job["result"])
    expected_min = scenes[0]["audio_duration"] + 3.0 + scenes[2]["audio_duration"]
    assert dur > expected_min, (dur, expected_min)  # lead/tail padding only adds time, never removes it
    print(f"[code-quiz] OK  duration={dur:.2f}s  file={mp4}")

    # Re-submitting the form (editing the question) must replace the 3 scenes, not accumulate extras.
    job = wait(c.post(f"/api/projects/{pid}/code-quiz",
                       data={"question": "A different question", "code": "", "answer": "A different answer"}).json())
    scenes2 = job["result"]["scenes"]
    assert len(scenes2) == 3, scenes2
    assert scenes2[0]["narration"] == "A different question"
    assert not scenes2[0]["content_image_url"], "no override uploaded this time, so none should remain"
    print("[code-quiz] OK  re-submitting replaces the 3 beat scenes without accumulating")

    # A voice/pace/pitch change must never re-synthesize the silent "wait" beat through TTS —
    # it has no narration, so TTS on it either collapses the 3s silence or hard-fails outright.
    c.patch(f"/api/projects/{pid}", json={"rate": 5})
    job = wait(c.post(f"/api/projects/{pid}/narration", json={}).json())
    scenes3 = c.get(f"/api/projects/{pid}").json()["scenes"]
    wait_scene = next(s for s in scenes3 if s["beat_type"] == "wait")
    assert abs(wait_scene["audio_duration"] - 3.0) < 0.01, wait_scene
    assert wait_scene["audio_current"], wait_scene
    job = wait(c.post(f"/api/projects/{pid}/render", json={}).json())
    assert job["status"] == "done", job
    print("[code-quiz] OK  voice/pace change preserves the wait beat's 3s silence and keeps rendering unblocked")

    assert not old_question_image.exists(), "replaced per-beat override image must be cleaned up from disk"
    print("[code-quiz] OK  replaced per-beat override image is deleted from disk")

    assert TRADEMARK_IMAGE.exists()  # the shared trademark file itself must survive scene replacement
    print("[code-quiz] OK  trademark image untouched by scene replacement")


def test_code_quiz_narration_toggle_and_theme():
    """Narration can be switched off (question/answer beats become silent placeholders of a
    configurable length) and the code block's theme/font size are settable per project."""
    c.put("/api/settings", json={"llm_provider": "offline", "image_provider": "offline", "tts_provider": "offline"})
    buf = io.BytesIO(); Image.new("RGB", (600, 1000), (10, 20, 30)).save(buf, "PNG")
    c.post("/api/settings/trademark-image", files={"file": ("bg.png", buf.getvalue(), "image/png")})

    pid = c.post("/api/projects", json={"content_type": "code_quiz", "language": "en"}).json()["id"]
    job = wait(c.post(f"/api/projects/{pid}/code-quiz", data={
        "question": "Q", "code": "let x = 5", "answer": "A",
        "narration_enabled": "false", "silent_beat_seconds": "4",
        "code_theme": "monokai", "code_font_size": "30",
    }).json())
    p = c.get(f"/api/projects/{pid}").json()
    assert p["narration_enabled"] is False and p["silent_beat_seconds"] == 4.0
    assert p["code_theme"] == "monokai" and p["code_font_size"] == 30
    scenes = job["result"]["scenes"]
    for sc in scenes:
        if sc["beat_type"] in ("question", "answer"):
            assert abs(sc["audio_duration"] - 4.0) < 0.01, sc
        elif sc["beat_type"] == "wait":
            assert abs(sc["audio_duration"] - 3.0) < 0.01, sc
    print("[code-quiz] OK  narration-off produces silent, configurable-length question/answer beats")

    job = wait(c.post(f"/api/projects/{pid}/render", json={}).json())
    assert job["status"] == "done", job
    print("[code-quiz] OK  renders cleanly with narration off and a non-default theme/font size")

    # Voice-step regeneration must respect the same narration_enabled/silent_beat_seconds setting.
    c.patch(f"/api/projects/{pid}", json={"pitch": 3})
    job = wait(c.post(f"/api/projects/{pid}/narration", json={}).json())
    scenes2 = c.get(f"/api/projects/{pid}").json()["scenes"]
    for sc in scenes2:
        if sc["beat_type"] in ("question", "answer"):
            assert abs(sc["audio_duration"] - 4.0) < 0.01, sc
    print("[code-quiz] OK  narration-off setting survives a Voice-step regeneration too")


def test_code_quiz_preview():
    """The Quiz step's preview endpoint renders a single composed still (background + title + code)
    without touching the database, for checking formatting before saving/rendering."""
    c.put("/api/settings", json={"image_provider": "offline"})
    buf = io.BytesIO(); Image.new("RGB", (600, 1000), (40, 60, 90)).save(buf, "PNG")
    c.post("/api/settings/trademark-image", files={"file": ("bg.png", buf.getvalue(), "image/png")})
    pid = c.post("/api/projects", json={"content_type": "code_quiz", "language": "en"}).json()["id"]

    r = c.post(f"/api/projects/{pid}/code-quiz/preview", data={
        "beat": "question", "text": "What does this log?\nA) 5\nB) 10", "code": "let x = 5",
        "show_title_card": "true",
    })
    assert r.status_code == 200, r.json()
    assert r.json()["url"].startswith("/cache/preview_beat_")

    r2 = c.post(f"/api/projects/{pid}/code-quiz/preview", data={"beat": "not-a-beat", "text": "x"})
    assert r2.status_code == 400

    with session() as s:
        assert not s.exec(select(Scene).where(Scene.project_id == pid)).all(), "preview must not create scenes"
    print("[code-quiz] OK  preview renders a still without touching project scenes")


def test_code_quiz_custom_html():
    """A per-beat raw-HTML override replaces the auto-generated heading entirely, is persisted and
    exposed via project_view, and renders cleanly end to end."""
    c.put("/api/settings", json={"tts_provider": "offline"})
    buf = io.BytesIO(); Image.new("RGB", (600, 1000), (40, 60, 90)).save(buf, "PNG")
    c.post("/api/settings/trademark-image", files={"file": ("bg.png", buf.getvalue(), "image/png")})
    pid = c.post("/api/projects", json={"content_type": "code_quiz", "language": "en"}).json()["id"]

    custom = '<div style="color:#fff;font-size:40px">My custom question!</div>'
    job = wait(c.post(f"/api/projects/{pid}/code-quiz", data={
        "question": "Q", "code": "let x = 5", "answer": "A", "question_html": custom,
    }).json())
    scenes = job["result"]["scenes"]
    q = next(s for s in scenes if s["beat_type"] == "question")
    a = next(s for s in scenes if s["beat_type"] == "answer")
    assert q["custom_html"] == custom, q
    assert a["custom_html"] == "", a

    job = wait(c.post(f"/api/projects/{pid}/render", json={}).json())
    assert job["status"] == "done", job
    print("[code-quiz] OK  per-beat raw-HTML override is stored and renders cleanly")

    r = c.post(f"/api/projects/{pid}/code-quiz/preview", data={
        "beat": "question", "text": "ignored when custom_html is set", "custom_html": custom,
    })
    assert r.status_code == 200, r.json()
    print("[code-quiz] OK  preview endpoint also supports the raw-HTML override")


if __name__ == "__main__":
    run("hi")
    run("en")
    test_self_recording_voice_change()
    test_code_quiz_flow()
    test_code_quiz_narration_toggle_and_theme()
    test_code_quiz_preview()
    test_code_quiz_custom_html()
    print("all good")
