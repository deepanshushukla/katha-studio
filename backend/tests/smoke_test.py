"""End-to-end smoke test using the offline providers (no internet needed).

Run from katha-studio/:  backend/.venv/bin/python backend/tests/smoke_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("KATHA_DATA", tempfile.mkdtemp(prefix="katha_test_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import FFMPEG, FFPROBE, PROJECTS  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Scene, session  # noqa: E402

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


if __name__ == "__main__":
    run("hi")
    run("en")
    test_self_recording_voice_change()
    print("all good")
