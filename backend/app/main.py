"""Katha Studio API."""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from . import config
from .config import CACHE, FRONTEND_DIST, MUSIC, PROJECTS
from .models import ImageVariant, Project, Scene, init_db, scenes_of, session, variants_of
from .pipeline import render as R
from .pipeline import scenes as SC
from .pipeline.captions import CAPTION_STYLES, HIGHLIGHT
from .providers import image as IMG
from .providers import llm as LLM
from .providers import tts as TTS
from .providers import voice_change as VC

app = FastAPI(title="Katha Studio")
init_db()


# ------------------------------------------------------------------ jobs
JOBS: dict[str, dict] = {}
_pool = ThreadPoolExecutor(max_workers=3)
_job_lock = threading.Lock()


def start_job(kind: str, project_id: int, fn) -> dict:
    with _job_lock:
        for j in JOBS.values():
            if j["project_id"] == project_id and j["kind"] == kind and j["status"] == "running":
                return j
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "project_id": project_id, "status": "running", "progress": 0.0,
               "message": "Starting…", "warnings": [], "result": None, "error": None, "started": time.time()}
        JOBS[jid] = job

    def run():
        try:
            job["result"] = fn(job)
            job["status"], job["progress"] = "done", 1.0
            job["message"] = job.get("message") if job["message"] != "Starting…" else "Done"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job["status"], job["error"] = "error", str(e)
    _pool.submit(run)
    return job


def job_public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k != "started"}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "job not found")
    return job_public(JOBS[jid])


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: int):
    return [job_public(j) for j in JOBS.values() if j["project_id"] == pid and j["status"] == "running"]


# ------------------------------------------------------------------ helpers
def pdir(pid: int) -> Path:
    d = PROJECTS / str(pid)
    for sub in ("images", "audio", "renders", "work"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def url_of(path: str | Path) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(PROJECTS.resolve())
        return f"/files/{rel.as_posix()}?v={int(p.stat().st_mtime)}" if p.exists() else ""
    except ValueError:
        rel = p.resolve().relative_to(CACHE.resolve())
        return f"/cache/{rel.as_posix()}"


def audio_key(text: str, p: Project) -> str:
    return hashlib.sha1(f"{text}|{p.tts_provider}|{p.voice}|{p.rate}|{p.pitch}".encode()).hexdigest()


def self_audio_key(text: str) -> str:
    return "self:" + hashlib.sha1(text.encode()).hexdigest()


def get_project(s, pid: int) -> Project:
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def touch(p: Project) -> None:
    p.updated_at = datetime.now(timezone.utc)


def project_view(pid: int) -> dict:
    with session() as s:
        p = get_project(s, pid)
        scs = scenes_of(s, pid)
        out_scenes = []
        for sc in scs:
            vs = variants_of(s, sc.id)
            out_scenes.append({
                "id": sc.id, "position": sc.position, "narration": sc.narration, "image_prompt": sc.image_prompt,
                "caption": sc.caption, "approved_image_id": sc.approved_image_id,
                "images": [{"id": v.id, "url": url_of(v.path), "seed": v.seed, "provider": v.provider,
                            "uploaded": v.uploaded} for v in vs],
                "audio_url": url_of(sc.audio_path) if sc.audio_path else "",
                "audio_duration": sc.audio_duration,
                "audio_self": sc.audio_self,
                "audio_current": bool(sc.audio_path) and (
                    sc.audio_key == self_audio_key(sc.narration) if sc.audio_self
                    else sc.audio_key == audio_key(sc.narration, p)),
                "words": json.loads(sc.words_json or "[]"),
            })
        d = p.model_dump()
        d["characters"] = p.characters
        d["render"] = {**R.DEFAULT_OPTIONS, **json.loads(p.render_json or "{}")}
        d["metadata"] = json.loads(p.metadata_json or "{}")
        d["last_render_url"] = url_of(p.last_render) if p.last_render else ""
        d["scenes"] = out_scenes
        d["style_prompt"] = SC.style_prompt(p.style_key, p.style_custom)
        return d


# ------------------------------------------------------------------ system / settings
@app.get("/api/health")
def health():
    return {"ffmpeg": config.ffmpeg_capabilities(), "mflux": bool(IMG._mflux_bin()), "version": "1.0"}


@app.get("/api/settings")
def get_settings():
    return config.public_settings()


@app.put("/api/settings")
def put_settings(patch: dict):
    config.save_settings(patch)
    return config.public_settings()


@app.get("/api/gemini/models")
def list_gemini_models():
    key = config.load_settings().get("gemini_api_key")
    if not key:
        raise HTTPException(400, "Add your Gemini API key first")
    try:
        return {"models": LLM.gemini_models(key)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not list models: {e}")


class TestReq(BaseModel):
    kind: str
    provider: Optional[str] = None


@app.post("/api/settings/test")
def test_provider(req: TestReq):
    s = config.load_settings()
    t0 = time.time()
    try:
        if req.kind == "llm":
            name = req.provider or s["llm_provider"]
            if name == "offline":
                return {"ok": True, "message": "Offline mode (no AI) – scenes are split from your sentences."}
            data = LLM.PROVIDERS[name]("Answer with JSON only.", 'Return {"ok": true, "word": "नमस्ते"}', s)
            return {"ok": True, "message": f"{name} replied {json.dumps(data, ensure_ascii=False)} in {time.time()-t0:.1f}s"}
        if req.kind == "image":
            name = req.provider or s["image_provider"]
            out = CACHE / f"test_{name}.png"
            IMG.PROVIDERS[name]("a golden lotus floating on a calm lake at sunrise, painting", 7, out, s)
            return {"ok": True, "message": f"{name} made an image in {time.time()-t0:.1f}s", "url": url_of(out)}
        if req.kind == "tts":
            name = req.provider or s["tts_provider"]
            voice = {"edge": "hi-IN-SwaraNeural", "offline": "test-beeps"}[name]
            out = CACHE / f"test_{name}.wav"
            TTS.PROVIDERS[name]("नमस्ते! यह आवाज़ की जाँच है।", voice, 0, 0, out)
            return {"ok": True, "message": f"{name} spoke in {time.time()-t0:.1f}s", "url": url_of(out)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)[:500]}
    raise HTTPException(400, "unknown kind")


@app.get("/api/catalog")
def catalog():
    return {"styles": [{"key": k, "label": v["label"], "prompt": v["prompt"]} for k, v in SC.STYLES.items()],
            "transitions": ["auto", "cut"] + R.TRANSITIONS, "caption_styles": CAPTION_STYLES,
            "highlights": list(HIGHLIGHT), "music": R.list_music()}


@app.get("/api/backgrounds")
def backgrounds():
    return [{"key": b["key"], "label": b["label"], "thumb": url_of(b["thumb_path"])} for b in R.list_backgrounds()]


# ------------------------------------------------------------------ projects
class NewProject(BaseModel):
    title: str = ""
    story: str = ""
    language: str = "hi"
    style_key: str = "cinematic"
    style_custom: str = ""
    target_seconds: int = 60
    content_type: str = "story"


@app.get("/api/projects")
def list_projects():
    with session() as s:
        out = []
        for p in s.exec(select(Project).order_by(Project.updated_at.desc())).all():
            scs = scenes_of(s, p.id)
            thumb = ""
            for sc in scs:
                if sc.approved_image_id:
                    v = s.get(ImageVariant, sc.approved_image_id)
                    thumb = url_of(v.path) if v else ""
                    break
            out.append({"id": p.id, "title": p.title, "language": p.language, "step": p.step,
                        "scenes": len(scs), "thumb": thumb, "updated_at": p.updated_at.isoformat(),
                        "has_video": bool(p.last_render)})
        return out


@app.post("/api/projects")
def create_project(req: NewProject):
    if req.content_type not in ("story", "code_quiz"):
        raise HTTPException(400, "content_type must be story or code_quiz")
    if req.language not in ("hi", "en"):
        raise HTTPException(400, "language must be hi or en")
    with session() as s:
        p = Project(title=req.title.strip() or "Untitled story", story=req.story, language=req.language,
                    style_key=req.style_key, style_custom=req.style_custom,
                    target_seconds=max(20, min(180, req.target_seconds)), seed=random.randint(1, 2**31 - 1),
                    content_type=req.content_type,
                    tts_provider=config.load_settings().get("tts_provider", "edge"),
                    voice="hi-IN-SwaraNeural" if req.language == "hi" else "en-IN-NeerjaExpressiveNeural")
        s.add(p); s.commit(); s.refresh(p)
        pdir(p.id)
        return {"id": p.id}


@app.get("/api/projects/{pid}")
def read_project(pid: int):
    return project_view(pid)


PATCHABLE = {"title", "story", "language", "style_key", "style_custom", "target_seconds", "tts_provider",
             "voice", "rate", "pitch", "step"}


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, patch: dict):
    with session() as s:
        p = get_project(s, pid)
        for k, v in patch.items():
            if k in PATCHABLE:
                if k == "step":
                    v = max(p.step, int(v))
                setattr(p, k, v)
            elif k == "characters":
                p.characters_json = json.dumps(v, ensure_ascii=False)
            elif k == "render":
                p.render_json = json.dumps({**json.loads(p.render_json or "{}"), **v}, ensure_ascii=False)
            elif k == "metadata":
                p.metadata_json = json.dumps(v, ensure_ascii=False)
        touch(p); s.add(p); s.commit()
    return project_view(pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    with session() as s:
        p = get_project(s, pid)
        for sc in scenes_of(s, pid):
            for v in variants_of(s, sc.id):
                s.delete(v)
            s.delete(sc)
        s.delete(p); s.commit()
    shutil.rmtree(PROJECTS / str(pid), ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------------ step 2: script
@app.post("/api/projects/{pid}/script")
def generate_script(pid: int):
    with session() as s:
        p = get_project(s, pid)
        story, lang, secs = p.story, p.language, p.target_seconds
    if not story.strip():
        raise HTTPException(400, "Story is empty")

    def work(job):
        job["message"] = "Writing the scene script…"
        data, provider, errors = SC.make_script(story, lang, secs)
        job["warnings"] = errors
        if not data["scenes"]:
            raise RuntimeError("Could not create any scenes")
        with session() as s:
            p = get_project(s, pid)
            for sc in scenes_of(s, pid):
                for v in variants_of(s, sc.id):
                    s.delete(v)
                s.delete(sc)
            for i, sc in enumerate(data["scenes"]):
                s.add(Scene(project_id=pid, position=i, narration=sc["narration"],
                            image_prompt=sc["image_prompt"], caption=sc["caption"]))
            p.characters_json = json.dumps(data["characters"], ensure_ascii=False)
            if data.get("title") and p.title in ("", "Untitled story"):
                p.title = data["title"]
            p.step = max(p.step, 2)
            touch(p); s.add(p); s.commit()
        job["message"] = f"Script ready ({len(data['scenes'])} scenes, via {provider})"
        return {"provider": provider}
    return job_public(start_job("script", pid, work))


class SceneIn(BaseModel):
    id: Optional[int] = None
    narration: str = ""
    image_prompt: str = ""
    caption: str = ""


@app.put("/api/projects/{pid}/scenes")
def save_scenes(pid: int, scenes: list[SceneIn]):
    with session() as s:
        get_project(s, pid)
        existing = {sc.id: sc for sc in scenes_of(s, pid)}
        keep = set()
        for i, sc in enumerate(scenes):
            if sc.id and sc.id in existing:
                row = existing[sc.id]
                row.narration, row.image_prompt, row.caption, row.position = sc.narration, sc.image_prompt, sc.caption, i
                keep.add(sc.id)
            else:
                row = Scene(project_id=pid, position=i, narration=sc.narration, image_prompt=sc.image_prompt,
                            caption=sc.caption)
            s.add(row)
        for sid, row in existing.items():
            if sid not in keep:
                for v in variants_of(s, sid):
                    s.delete(v)
                s.delete(row)
        s.commit()
    return project_view(pid)


# ------------------------------------------------------------------ step 3: images
class ImageReq(BaseModel):
    scene_ids: Optional[list[int]] = None
    variants: int = 2
    only_missing: bool = True
    new_seed: bool = False


def _gen_images(pid: int, req: ImageReq, job: dict) -> dict:
    with session() as s:
        p = get_project(s, pid)
        chars, style, base_seed = p.characters, SC.style_prompt(p.style_key, p.style_custom), p.seed
        scs = [sc for sc in scenes_of(s, pid) if req.scene_ids is None or sc.id in req.scene_ids]
        todo = []
        for sc in scs:
            have = len(variants_of(s, sc.id))
            if req.only_missing and have > 0:
                continue
            todo.append((sc.id, sc.image_prompt or sc.narration, have))
    total = len(todo) * max(1, req.variants)
    made = 0
    used = set()
    for sid, sp, have in todo:
        prompt = SC.full_image_prompt(sp, chars, style)
        for k in range(max(1, req.variants)):
            # same seed across scenes for a given variant slot keeps the look consistent
            seed = random.randint(1, 2**31 - 1) if req.new_seed else base_seed + (have + k) * 7919
            out = pdir(pid) / "images" / f"scene_{sid}_{uuid.uuid4().hex[:8]}.png"
            job["message"] = f"Painting image {made + 1}/{total}…"
            try:
                provider, errs = IMG.generate(prompt, seed, out)
                if errs:
                    job["warnings"].append(f"Fell back to {provider}: " + "; ".join(errs))
            except Exception as e:  # noqa: BLE001
                job["warnings"].append(f"Scene image failed: {e}")
                made += 1
                job["progress"] = made / total
                continue
            used.add(provider)
            with session() as s:
                v = ImageVariant(scene_id=sid, path=str(out), prompt=prompt, seed=seed, provider=provider)
                s.add(v); s.commit()
            made += 1
            job["progress"] = made / total
    with session() as s:
        p = get_project(s, pid); p.step = max(p.step, 3); touch(p); s.add(p); s.commit()
    job["message"] = f"Made {made} image(s)" + (f" with {', '.join(sorted(used))}" if used else "")
    if total and not used:
        raise RuntimeError("No images could be generated. " + " | ".join(job["warnings"][-3:]))
    return {"made": made}


@app.post("/api/projects/{pid}/images")
def generate_images(pid: int, req: ImageReq):
    return job_public(start_job("images", pid, lambda job: _gen_images(pid, req, job)))


@app.post("/api/scenes/{sid}/images")
def regenerate_scene_images(sid: int, req: ImageReq):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        pid = sc.project_id
    req.scene_ids, req.only_missing = [sid], False
    return job_public(start_job(f"images:{sid}", pid, lambda job: _gen_images(pid, req, job)))


class Approve(BaseModel):
    image_id: Optional[int]


@app.post("/api/scenes/{sid}/approve")
def approve_image(sid: int, req: Approve):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        sc.approved_image_id = req.image_id
        s.add(sc); s.commit()
        pid = sc.project_id
    return project_view(pid)


@app.post("/api/scenes/{sid}/upload")
async def upload_image(sid: int, file: UploadFile = File(...)):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        pid = sc.project_id
    data = await file.read()
    out = pdir(pid) / "images" / f"scene_{sid}_upload_{uuid.uuid4().hex[:8]}.png"
    try:
        IMG._save_bytes(data, out)
    except Exception:
        raise HTTPException(400, "That file is not an image")
    with session() as s:
        v = ImageVariant(scene_id=sid, path=str(out), prompt="(uploaded)", provider="upload", uploaded=True)
        s.add(v); s.commit(); s.refresh(v)
        sc = s.get(Scene, sid); sc.approved_image_id = v.id; s.add(sc); s.commit()
    return project_view(pid)


@app.post("/api/scenes/{sid}/upload-reference")
async def upload_reference(sid: int, file: UploadFile = File(...), strength: float = Form(0.55)):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        pid = sc.project_id
        p = get_project(s, pid)
        chars, style = p.characters, SC.style_prompt(p.style_key, p.style_custom)
        prompt = SC.full_image_prompt(sc.image_prompt or sc.narration, chars, style)
        seed = p.seed + len(variants_of(s, sid)) * 7919
    data = await file.read()
    ref = pdir(pid) / "images" / f"scene_{sid}_ref_{uuid.uuid4().hex[:8]}.png"
    try:
        IMG._save_bytes(data, ref)
    except Exception:
        raise HTTPException(400, "That file is not an image")

    def work(job):
        job["message"] = "Repainting your photo…"
        out = pdir(pid) / "images" / f"scene_{sid}_{uuid.uuid4().hex[:8]}.png"
        try:
            provider = IMG.generate_from_reference(prompt, seed, out, ref, strength)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Could not stylise that photo: {e}")
        with session() as s:
            v = ImageVariant(scene_id=sid, path=str(out), prompt=prompt, seed=seed, provider=provider)
            s.add(v); s.commit()
        job["message"] = "Styled image ready"
        return project_view(pid)
    return job_public(start_job(f"img2img:{sid}", pid, work))


@app.delete("/api/images/{iid}")
def delete_image(iid: int):
    with session() as s:
        v = s.get(ImageVariant, iid)
        if not v:
            raise HTTPException(404, "image not found")
        sc = s.get(Scene, v.scene_id)
        if sc and sc.approved_image_id == iid:
            sc.approved_image_id = None; s.add(sc)
        Path(v.path).unlink(missing_ok=True)
        s.delete(v); s.commit()
        pid = sc.project_id if sc else None
    return project_view(pid) if pid else {"ok": True}


# ------------------------------------------------------------------ step 4: voice
@app.get("/api/voices")
def get_voices(lang: str = "hi"):
    return TTS.voices(lang)


SAMPLE = {"hi": "नमस्ते! आइए सुनते हैं एक अद्भुत पौराणिक कथा, जो आपके मन को छू लेगी।",
          "en": "Namaste! Let me tell you a wonderful story from Indian mythology that will stay with you."}


class PreviewReq(BaseModel):
    provider: str
    voice: str
    rate: int = 0
    pitch: int = 0
    lang: str = "hi"
    text: Optional[str] = None


@app.post("/api/voices/preview")
def preview_voice(req: PreviewReq):
    text = (req.text or SAMPLE.get(req.lang, SAMPLE["en"]))[:400]
    key = hashlib.sha1(f"{text}|{req.provider}|{req.voice}|{req.rate}|{req.pitch}".encode()).hexdigest()[:16]
    out = CACHE / f"preview_{key}.wav"
    if not out.exists():
        try:
            TTS.PROVIDERS[req.provider](text, req.voice, req.rate, req.pitch, out)
        except Exception as e:  # noqa: BLE001
            out.unlink(missing_ok=True)
            raise HTTPException(502, f"Voice preview failed: {e}")
    return {"url": url_of(out)}


class NarrationReq(BaseModel):
    scene_ids: Optional[list[int]] = None
    force: bool = False


@app.post("/api/projects/{pid}/narration")
def generate_narration(pid: int, req: NarrationReq):
    def work(job):
        with session() as s:
            p = get_project(s, pid)
            prov, voice, rate, pitch = p.tts_provider, p.voice, p.rate, p.pitch
            todo = [(sc.id, sc.narration, audio_key(sc.narration, p)) for sc in scenes_of(s, pid)
                    if (req.scene_ids is None or sc.id in req.scene_ids)
                    and not (sc.audio_self and req.scene_ids is None and not req.force)
                    and (req.force or sc.audio_key != audio_key(sc.narration, p) or not sc.audio_path)]
        for i, (sid, text, key) in enumerate(todo):
            job["message"] = f"Recording scene {i + 1}/{len(todo)}…"
            out = pdir(pid) / "audio" / f"scene_{sid}_{key[:10]}.wav"
            dur, words, used = TTS.synth(text, prov, voice, rate, pitch, out)
            if used != prov:
                job["warnings"].append(f"Scene {i + 1}: {prov} failed, used {used} instead")
            with session() as s:
                sc = s.get(Scene, sid)
                if sc.audio_path and sc.audio_path != str(out):
                    Path(sc.audio_path).unlink(missing_ok=True)
                sc.audio_path, sc.audio_duration, sc.audio_key, sc.audio_self = str(out), dur, key, False
                sc.words_json = json.dumps(words, ensure_ascii=False)
                s.add(sc); s.commit()
            job["progress"] = (i + 1) / max(1, len(todo))
        with session() as s:
            p = get_project(s, pid); p.step = max(p.step, 4); touch(p); s.add(p); s.commit()
        job["message"] = f"Recorded {len(todo)} scene(s)" if todo else "All narration already up to date"
        return {"recorded": len(todo)}
    return job_public(start_job("narration", pid, work))


@app.post("/api/scenes/{sid}/record-audio")
async def record_audio(sid: int, file: UploadFile = File(...)):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        pid, text = sc.project_id, sc.narration
        old_path, old_original = sc.audio_path, sc.audio_original_path
    raw = CACHE / f"recording_{uuid.uuid4().hex[:12]}.bin"
    raw.write_bytes(await file.read())

    def work(job):
        try:
            out = pdir(pid) / "audio" / f"scene_{sid}_self_{uuid.uuid4().hex[:8]}.wav"
            TTS.to_wav(raw, out)
        finally:
            raw.unlink(missing_ok=True)
        dur = TTS.duration_of(out)
        begin, finish = TTS.speech_bounds(out, dur)
        words = TTS.estimate_words(text, begin, finish)
        with session() as s:
            sc = s.get(Scene, sid)
            if old_path and old_path != str(out):
                Path(old_path).unlink(missing_ok=True)
            if old_original and old_original not in (old_path, str(out)):
                Path(old_original).unlink(missing_ok=True)
            sc.audio_path, sc.audio_original_path, sc.audio_duration = str(out), str(out), dur
            sc.audio_key, sc.audio_self = self_audio_key(text), True
            sc.words_json = json.dumps(words, ensure_ascii=False)
            s.add(sc); s.commit()
        return project_view(pid)
    return job_public(start_job(f"record:{sid}", pid, work))


@app.post("/api/scenes/{sid}/change-voice")
async def change_voice(sid: int, ref_file: Optional[UploadFile] = File(None),
                        provider: str = Form(""), voice: str = Form(""),
                        rate: int = Form(0), pitch: int = Form(0)):
    with session() as s:
        sc = s.get(Scene, sid)
        if not sc:
            raise HTTPException(404, "scene not found")
        pid, text = sc.project_id, sc.narration
        if not sc.audio_self or not sc.audio_path or not Path(sc.audio_path).exists():
            raise HTTPException(400, "Record your voice for this scene first")
        # Always re-process from the pristine recording, never from a previous apply's output —
        # otherwise repeated pace/pitch/voice changes compound on each other.
        src = Path(sc.audio_original_path or sc.audio_path)
        if not src.exists():
            src = Path(sc.audio_path)
        lang = get_project(s, pid).language

    ref_bytes = await ref_file.read() if ref_file is not None else None
    want_voice_change = ref_bytes is not None or bool(provider and voice)
    if not want_voice_change and not rate and not pitch:
        raise HTTPException(400, "Choose a target voice, upload a reference clip, or adjust pace/pitch")

    def work(job):
        # TTS/ffmpeg calls below may themselves need an event loop (e.g. Edge TTS) — must run off the
        # request's own async loop, so all of this lives in the background-job thread, not the endpoint.
        current = src
        if want_voice_change:
            if ref_bytes is not None:
                ref_raw = CACHE / "voice_refs" / f"{uuid.uuid4().hex[:10]}.raw"
                ref_raw.parent.mkdir(parents=True, exist_ok=True)
                ref_raw.write_bytes(ref_bytes)
                ref = ref_raw.with_suffix(".wav")
                try:
                    TTS.to_wav(ref_raw, ref)
                finally:
                    ref_raw.unlink(missing_ok=True)
            else:
                sample = SAMPLE.get(lang, SAMPLE["en"])
                key = hashlib.sha1(f"{sample}|{provider}|{voice}|0|0".encode()).hexdigest()[:16]
                ref = CACHE / f"preview_{key}.wav"
                if not ref.exists():
                    TTS.PROVIDERS[provider](sample, voice, 0, 0, ref)

            job["message"] = "Changing voice…"
            vc_out = pdir(pid) / "audio" / f"scene_{sid}_vc_{uuid.uuid4().hex[:8]}.wav"
            raw_out = vc_out.with_suffix(".raw.wav")
            try:
                VC.change_voice(current, ref, raw_out)
                TTS.to_wav(raw_out, vc_out)
            finally:
                raw_out.unlink(missing_ok=True)
            current = vc_out

        if rate or pitch:
            job["message"] = "Adjusting pace/pitch…"
            adj_out = pdir(pid) / "audio" / f"scene_{sid}_adj_{uuid.uuid4().hex[:8]}.wav"
            TTS.apply_rate_pitch(current, adj_out, rate, pitch)
            if current != src:
                current.unlink(missing_ok=True)
            current = adj_out

        out = current
        dur = TTS.duration_of(out)
        begin, finish = TTS.speech_bounds(out, dur)
        words = TTS.estimate_words(text, begin, finish)
        with session() as s:
            sc = s.get(Scene, sid)
            # Never delete the pristine original — only intermediate outputs from a previous apply.
            if sc.audio_path and sc.audio_path != str(out) and sc.audio_path != sc.audio_original_path:
                Path(sc.audio_path).unlink(missing_ok=True)
            sc.audio_path, sc.audio_duration = str(out), dur
            sc.audio_key, sc.audio_self = self_audio_key(text), True
            sc.words_json = json.dumps(words, ensure_ascii=False)
            s.add(sc); s.commit()
        job["message"] = "Updated"
        return project_view(pid)
    return job_public(start_job(f"voicechange:{sid}", pid, work))


# ------------------------------------------------------------------ step 5: render
@app.get("/api/music")
def music_list():
    return {"tracks": [{"name": n, "url": f"/music/{n}"} for n in R.list_music()]}


@app.post("/api/music/upload")
async def music_upload(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "track.mp3").name)
    if Path(name).suffix.lower() not in R.MUSIC_EXT:
        raise HTTPException(400, "Use mp3, wav, m4a, aac, ogg or flac")
    (MUSIC / name).write_bytes(await file.read())
    return {**music_list(), "name": name}


@app.post("/api/projects/{pid}/metadata")
def gen_metadata(pid: int):
    def work(job):
        with session() as s:
            p = get_project(s, pid)
            title, story, lang = p.title, p.story, p.language
            narration = " ".join(sc.narration for sc in scenes_of(s, pid))
        job["message"] = "Writing title & hashtags…"
        meta = SC.make_metadata(title, story, narration, lang)
        with session() as s:
            p = get_project(s, pid); p.metadata_json = json.dumps(meta, ensure_ascii=False); s.add(p); s.commit()
        job["message"] = "Metadata ready"
        return meta
    return job_public(start_job("metadata", pid, work))


@app.post("/api/projects/{pid}/render")
def render_video(pid: int, options: dict):
    with session() as s:
        p = get_project(s, pid)
        if options:
            p.render_json = json.dumps({**json.loads(p.render_json or "{}"), **options}, ensure_ascii=False)
            s.add(p); s.commit()
        opts = {**R.DEFAULT_OPTIONS, **json.loads(p.render_json or "{}")}
        lang, seed, title = p.language, p.seed, p.title
        need_images = opts.get("visual_mode") != "background"
        problems, items = [], []
        for i, sc in enumerate(scenes_of(s, pid)):
            v = s.get(ImageVariant, sc.approved_image_id) if sc.approved_image_id else None
            if need_images and (not v or not Path(v.path).exists()):
                problems.append(f"scene {i + 1} has no approved image")
            audio_ok = sc.audio_key == self_audio_key(sc.narration) if sc.audio_self else sc.audio_key == audio_key(sc.narration, p)
            if not sc.audio_path or not audio_ok or not Path(sc.audio_path).exists():
                problems.append(f"scene {i + 1} narration is missing or out of date")
            if (v or not need_images) and sc.audio_path:
                items.append({"image": v.path if v else "", "audio": sc.audio_path, "duration": sc.audio_duration,
                              "words": json.loads(sc.words_json or "[]")})
        if not items:
            problems.append("no scenes")
    if problems:
        raise HTTPException(400, "Can't render yet: " + "; ".join(problems))
    if opts.get("show_title") and not opts.get("title_text"):
        opts["title_text"] = title

    def work(job):
        d = pdir(pid)
        work_dir = d / "work" / uuid.uuid4().hex[:8]
        slug = re.sub(r"[^\w\u0900-\u097F]+", "_", title).strip("_")[:40] or "katha"
        out = d / "renders" / f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"

        def prog(f, msg):
            job["progress"], job["message"] = round(f, 3), msg
        info = R.render(items, lang, opts, work_dir, out, prog, seed=seed)
        shutil.rmtree(work_dir, ignore_errors=True)
        with session() as s:
            p = get_project(s, pid); p.last_render = str(out); p.step = 5; touch(p); s.add(p); s.commit()
        job["message"] = f"Video ready ({info['duration']:.1f}s)"
        return {"url": url_of(out), "duration": info["duration"]}
    return job_public(start_job("render", pid, work))


@app.get("/api/projects/{pid}/renders")
def list_renders(pid: int):
    d = pdir(pid) / "renders"
    files = sorted(d.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "url": url_of(f), "size_mb": round(f.stat().st_size / 1e6, 1)} for f in files]


# ------------------------------------------------------------------ static
app.mount("/files", StaticFiles(directory=PROJECTS), name="files")
app.mount("/cache", StaticFiles(directory=CACHE), name="cache")
app.mount("/music", StaticFiles(directory=MUSIC), name="music")

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
