# Code Quiz Reels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second content type to Katha Studio — "Code Quiz" reels (question → 3-2-1 wait → answer/explanation), authored directly by the user (no LLM), rendered with a fixed trademark background image, a default dramatic music track, and an on-screen code block + question/answer heading overlay.

**Architecture:** Reuse the existing `Scene`/`ImageVariant`/job/render pipeline exactly as the mythology flow does — a code-quiz project is just a `Project` with `content_type="code_quiz"` and exactly 3 `Scene` rows (`beat_type` = `question`/`wait`/`answer`) created directly from user input instead of via the LLM `/script` step. Images skip AI generation and point at a fixed trademark file. The only genuinely new code is: (a) the scene-creation endpoint, (b) a PIL-based overlay (code block + heading) baked into each scene's still image before the existing Ken Burns/ffmpeg compositing, and (c) an ffmpeg `drawtext` countdown for the `wait` beat's clip.

**Tech Stack:** FastAPI + SQLModel (existing), Pillow (existing), Pygments (new, for code tokenization — rendering stays PIL-based, not Pygments' own image formatter, so font handling stays under our control), ffmpeg (existing), React (existing).

**Spec:** `docs/superpowers/specs/2026-10-03-code-quiz-reels-design.md`

## Global Constraints

- No LLM call anywhere in the code-quiz content path — question/code/answer text is used verbatim, exactly as the spec requires (§1, §4.1).
- Existing "story" project behavior must be byte-for-byte unaffected — every new `Scene`/`Project` field defaults such that `.get(...)`/falsy checks on old data are no-ops (spec §2 "out of scope" + Review Focus #5 below).
- Quick-fire (multiple question/answer groups) is explicitly **not** built now — don't add loops, lists-of-beats, or multi-group scaffolding; 3 scenes per code-quiz project, full stop (spec §2).
- In-app math/LaTeX rendering is explicitly **not** built now — the per-beat `content_image_path` override is a plain file upload, nothing generates images from text (spec §2, §9).
- Countdown, code-block style, and title-card style are fixed/not configurable yet (spec §2) — don't add settings/toggles for them beyond the single `show_title_card` boolean the spec already calls for.

## Review Focus

- No trademark image anywhere (no global default, no per-request override) when creating a code-quiz video → must return a clear `400`, never a crash or a render with a missing image (spec §4.1 step 2, §8).
- Re-submitting `/code-quiz` for a project that already has scenes (editing the question later) must replace the old 3 scenes and their audio/image files cleanly, not accumulate duplicates or leak orphaned files on disk (spec §4.1 step 4 implies replace-in-place, same as `/script` does for story projects).
- A story-type project's render must be pixel-for-pixel/behavior-identical after this change — `beat_type`/`code_text` etc. are always absent/falsy on story scenes, so the new overlay and countdown code paths must never trigger for them (spec §2, "out of scope").
- Long question/code/answer text must wrap inside the overlay box instead of overflowing the frame or raising a PIL/ffmpeg error (spec §8).
- Text containing characters that are special to ffmpeg's filter syntax or ASS captions (`:`, `'`, `\`, `,`, non-ASCII) must not break the countdown `drawtext` filter or the render (the countdown filter is new ffmpeg-filter-string construction, the single riskiest piece of string-building in this plan).

---

### Task 1: Data model — `content_type` and beat fields

**Files:**
- Modify: `backend/app/models.py:15-56` (`Project`, `Scene`), `backend/app/models.py:70-79` (`init_db`)

**Interfaces:**
- Produces: `Project.content_type: str` (`"story"` | `"code_quiz"`), `Scene.beat_type: str` (`""`/`"question"`/`"wait"`/`"answer"`), `Scene.code_text: str`, `Scene.show_title_card: bool`, `Scene.content_image_path: str`.

- [ ] **Step 1: Add the new columns**

Edit `backend/app/models.py`. In `Project` (after `style_custom: str = ""` on line 21):

```python
    content_type: str = "story"          # story | code_quiz
```

In `Scene` (after `words_json: str = "[]"` on line 56):

```python
    beat_type: str = ""                  # "" (story scene) | question | wait | answer
    code_text: str = ""                  # code snippet to overlay (question/answer beats)
    show_title_card: bool = True         # overlay the beat's narration as an on-screen heading
    content_image_path: str = ""         # optional per-beat image override (math/diagram cases)
```

- [ ] **Step 2: Add the migration**

In `init_db()` (`backend/app/models.py:70-79`), add after the existing two `if` blocks but before `def session()`:

```python
        if "content_type" not in {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(project)").fetchall()}:
            conn.exec_driver_sql("ALTER TABLE project ADD COLUMN content_type TEXT DEFAULT 'story'")
            conn.commit()
        if "beat_type" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN beat_type TEXT DEFAULT ''")
            conn.commit()
        if "code_text" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN code_text TEXT DEFAULT ''")
            conn.commit()
        if "show_title_card" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN show_title_card BOOLEAN DEFAULT 1")
            conn.commit()
        if "content_image_path" not in cols:
            conn.exec_driver_sql("ALTER TABLE scene ADD COLUMN content_image_path TEXT DEFAULT ''")
            conn.commit()
```

(`cols` is the existing `scene` table-info set already computed on line 73 — reuse it, don't recompute.)

- [ ] **Step 3: Verify the migration runs clean**

Run: `rm -f /tmp/katha_migration_check.db && KATHA_DATA=/tmp/katha_migration_check backend/.venv/bin/python -c "
from app.models import init_db, session, Project, Scene
init_db()
with session() as s:
    p = Project(story='x'); s.add(p); s.commit(); s.refresh(p)
    print(p.content_type)
    sc = Scene(project_id=p.id, beat_type='question'); s.add(sc); s.commit(); s.refresh(sc)
    print(sc.beat_type, sc.code_text, sc.show_title_card, sc.content_image_path)
"`
(run from `backend/`)
Expected output: `story` then `question  True `

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py
git commit -m "feat: add content_type and beat fields for code-quiz scenes"
```

---

### Task 2: Config — trademark image path + default music setting

**Files:**
- Modify: `backend/app/config.py:11-23` (paths), `backend/app/config.py:51-73` (`DEFAULTS`)

**Interfaces:**
- Produces: `config.TRADEMARK_DIR: Path`, `config.TRADEMARK_IMAGE: Path` (fixed file, `TRADEMARK_DIR / "background.png"`), `DEFAULTS["default_code_quiz_music"] = ""`.
- Consumes: nothing new.

- [ ] **Step 1: Add the paths**

In `backend/app/config.py`, after line 18 (`FRONTEND_DIST = ROOT / "frontend" / "dist"`):

```python
TRADEMARK_DIR = DATA / "trademark"
TRADEMARK_IMAGE = TRADEMARK_DIR / "background.png"
```

Change line 22-23 from:
```python
for p in (DATA, PROJECTS, CACHE, MUSIC):
    p.mkdir(parents=True, exist_ok=True)
```
to:
```python
for p in (DATA, PROJECTS, CACHE, MUSIC, TRADEMARK_DIR):
    p.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Add the settings default**

In `DEFAULTS` (`backend/app/config.py:51-73`), add under the `# Voice` section (after `"auto_fallback": True,` on line 72):

```python
    # Code quiz
    "default_code_quiz_music": "",       # filename inside MUSIC, picked by default for new code_quiz projects
```

- [ ] **Step 3: Verify**

Run: `backend/.venv/bin/python -c "from app import config; print(config.TRADEMARK_DIR.exists(), config.load_settings()['default_code_quiz_music'])"` (from `backend/`)
Expected output: `True ` (empty string after the space)

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add trademark image path and default code-quiz music setting"
```

---

### Task 3: TTS — silent-clip helper for the wait beat

**Files:**
- Modify: `backend/app/providers/tts.py` (add function near `to_wav`, after line 66)
- Test: ad hoc, see Step 1

**Interfaces:**
- Produces: `TTS.make_silence(out_wav: Path, seconds: float, sr: int = 48000) -> None`.
- Consumes: nothing new (uses the already-imported `wave` module).

- [ ] **Step 1: Write a throwaway check that the function doesn't exist yet**

Run: `backend/.venv/bin/python -c "from app.providers import tts; tts.make_silence"` (from `backend/`)
Expected: `AttributeError: module 'app.providers.tts' has no attribute 'make_silence'`

- [ ] **Step 2: Implement it**

In `backend/app/providers/tts.py`, add after `to_wav()` (after line 66):

```python
def make_silence(out_wav: Path, seconds: float, sr: int = 48000) -> None:
    """A silent mono WAV of the given length — used for the code-quiz "wait" beat, which has no narration."""
    n = int(sr * seconds)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
```

- [ ] **Step 3: Verify**

Run: `backend/.venv/bin/python -c "
from pathlib import Path
from app.providers import tts
out = Path('/tmp/silence_check.wav')
tts.make_silence(out, 3.0)
print(round(tts.duration_of(out), 2))
"` (from `backend/`)
Expected output: `3.0`

- [ ] **Step 4: Commit**

```bash
git add backend/app/providers/tts.py
git commit -m "feat: add make_silence helper for the code-quiz wait beat"
```

---

### Task 4: Project creation accepts `content_type`

**Files:**
- Modify: `backend/app/main.py:225-264` (`NewProject`, `create_project`)

**Interfaces:**
- Consumes: `Project.content_type` (Task 1).
- Produces: `POST /api/projects` accepts `content_type: "story" | "code_quiz"`; `story` is now optional.

- [ ] **Step 1: Write a throwaway check**

Run (from `backend/`): `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
r = c.post('/api/projects', json={'content_type': 'code_quiz', 'language': 'en'})
print(r.status_code, r.json())
"`
Expected: currently fails with `422` because `story` has no default (required field).

- [ ] **Step 2: Implement**

In `backend/app/main.py`, change `NewProject` (lines 225-231) from:

```python
class NewProject(BaseModel):
    title: str = ""
    story: str
    language: str = "hi"
    style_key: str = "cinematic"
    style_custom: str = ""
    target_seconds: int = 60
```

to:

```python
class NewProject(BaseModel):
    title: str = ""
    story: str = ""
    language: str = "hi"
    style_key: str = "cinematic"
    style_custom: str = ""
    target_seconds: int = 60
    content_type: str = "story"
```

Change `create_project` (lines 252-264) from:

```python
@app.post("/api/projects")
def create_project(req: NewProject):
    if req.language not in ("hi", "en"):
        raise HTTPException(400, "language must be hi or en")
    with session() as s:
        p = Project(title=req.title.strip() or "Untitled story", story=req.story, language=req.language,
                    style_key=req.style_key, style_custom=req.style_custom,
                    target_seconds=max(20, min(180, req.target_seconds)), seed=random.randint(1, 2**31 - 1),
                    tts_provider=config.load_settings().get("tts_provider", "edge"),
                    voice="hi-IN-SwaraNeural" if req.language == "hi" else "en-IN-NeerjaExpressiveNeural")
        s.add(p); s.commit(); s.refresh(p)
        pdir(p.id)
        return {"id": p.id}
```

to:

```python
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
```

- [ ] **Step 3: Verify**

Re-run the Step 1 command. Expected: `200 {'id': <some int>}`.
Also re-run the existing story-creation path still works: `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
r = c.post('/api/projects', json={'story': 'a story', 'language': 'hi'})
print(r.status_code, r.json())
"` — expected `200 {'id': <int>}`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: let project creation set content_type=code_quiz"
```

---

### Task 5: Trademark image upload + exposure

**Files:**
- Modify: `backend/app/main.py:212-216` (`catalog`), `backend/app/main.py:791-794` (static mounts)

**Interfaces:**
- Consumes: `config.TRADEMARK_DIR`, `config.TRADEMARK_IMAGE` (Task 2), `IMG._save_bytes` (existing, used by `upload_image`).
- Produces: `POST /api/settings/trademark-image`; `GET /api/catalog` gains `"trademark_url"`.

- [ ] **Step 1: Write a throwaway check**

Run (from `backend/`): `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
print(c.get('/api/catalog').json().get('trademark_url'))
"`
Expected: currently `KeyError`-free but prints `None` (key doesn't exist yet) — confirms the field is missing.

- [ ] **Step 2: Mount the static dir and add the upload endpoint**

In `backend/app/main.py`, add the mount next to the existing ones (after line 794, `app.mount("/music", ...)`):

```python
app.mount("/trademark", StaticFiles(directory=config.TRADEMARK_DIR), name="trademark")
```

Add the upload endpoint near the other settings endpoints, after `test_provider` (after line 209, before `@app.get("/api/catalog")`):

```python
@app.post("/api/settings/trademark-image")
async def upload_trademark_image(file: UploadFile = File(...)):
    data = await file.read()
    try:
        IMG._save_bytes(data, config.TRADEMARK_IMAGE)
    except Exception:
        raise HTTPException(400, "That file is not an image")
    return {"trademark_url": _trademark_url()}
```

Add the small helper just above it (also before `catalog`):

```python
def _trademark_url() -> str:
    tm = config.TRADEMARK_IMAGE
    return f"/trademark/{tm.name}?v={int(tm.stat().st_mtime)}" if tm.exists() else ""
```

- [ ] **Step 3: Expose it from `/api/catalog`**

Change `catalog()` (lines 212-216) from:

```python
@app.get("/api/catalog")
def catalog():
    return {"styles": [{"key": k, "label": v["label"], "prompt": v["prompt"]} for k, v in SC.STYLES.items()],
            "transitions": ["auto", "cut"] + R.TRANSITIONS, "caption_styles": CAPTION_STYLES,
            "highlights": list(HIGHLIGHT), "music": R.list_music()}
```

to:

```python
@app.get("/api/catalog")
def catalog():
    return {"styles": [{"key": k, "label": v["label"], "prompt": v["prompt"]} for k, v in SC.STYLES.items()],
            "transitions": ["auto", "cut"] + R.TRANSITIONS, "caption_styles": CAPTION_STYLES,
            "highlights": list(HIGHLIGHT), "music": R.list_music(), "trademark_url": _trademark_url()}
```

- [ ] **Step 4: Verify**

Run (from `backend/`): `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
print('before:', c.get('/api/catalog').json()['trademark_url'])
png = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a4944415478da6360000002000100') # silence cases below use PIL instead for reliability
"`

Instead, verify with a real PIL-generated PNG (more reliable than a hand-rolled byte string):

Run: `.venv/bin/python -c "
import io
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
buf = io.BytesIO()
Image.new('RGB', (40, 40), (10, 20, 30)).save(buf, 'PNG')
r = c.post('/api/settings/trademark-image', files={'file': ('bg.png', buf.getvalue(), 'image/png')})
print(r.status_code, r.json())
print('after:', c.get('/api/catalog').json()['trademark_url'])
"`
Expected: `200 {'trademark_url': '/trademark/background.png?v=...'}` then `after: /trademark/background.png?v=...`

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: add trademark background image upload, expose via /api/catalog"
```

---

### Task 6: `POST /api/projects/{pid}/code-quiz`

**Files:**
- Modify: `backend/app/main.py` (add after the `record-audio`/`change-voice` block, before the `# --- step 5: render` comment on line 708)

**Interfaces:**
- Consumes: `Scene.beat_type/code_text/show_title_card/content_image_path` (Task 1), `config.TRADEMARK_IMAGE` (Task 2), `TTS.make_silence` (Task 3), `IMG._save_bytes` (existing), `audio_key()` (existing, `main.py:103-104`).
- Produces: `POST /api/projects/{pid}/code-quiz` — creates/replaces the 3 beat scenes and their narration/silence audio, returns a job.

- [ ] **Step 1: Write a throwaway check that the endpoint 404s today**

Run (from `backend/`): `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
pid = c.post('/api/projects', json={'content_type': 'code_quiz', 'language': 'en'}).json()['id']
r = c.post(f'/api/projects/{pid}/code-quiz', data={'question': 'q', 'answer': 'a'})
print(r.status_code)
"`
Expected: `404` (route doesn't exist).

- [ ] **Step 2: Implement**

Add to `backend/app/main.py`, right before the `# ------------------------------------------------------------------ step 5: render` comment (line 708):

```python
@app.post("/api/projects/{pid}/code-quiz")
async def set_code_quiz(pid: int,
                         question: str = Form(...),
                         answer: str = Form(...),
                         code: str = Form(""),
                         show_title_card: bool = Form(True),
                         music: str = Form(""),
                         question_image: Optional[UploadFile] = File(None),
                         answer_image: Optional[UploadFile] = File(None),
                         background_image: Optional[UploadFile] = File(None)):
    with session() as s:
        p = get_project(s, pid)
        if p.content_type != "code_quiz":
            raise HTTPException(400, "project is not a code_quiz project")
    if not question.strip() or not answer.strip():
        raise HTTPException(400, "Question and answer are required")

    if background_image is not None:
        data = await background_image.read()
        bg_path = pdir(pid) / "images" / "background_override.png"
        try:
            IMG._save_bytes(data, bg_path)
        except Exception:
            raise HTTPException(400, "Background image is not a valid image")
    elif config.TRADEMARK_IMAGE.exists():
        bg_path = config.TRADEMARK_IMAGE
    else:
        raise HTTPException(400, "Set a trademark background image in Settings first, or upload one for this video")

    async def save_optional(f: Optional[UploadFile]) -> str:
        if f is None:
            return ""
        data = await f.read()
        out = pdir(pid) / "images" / f"beat_{uuid.uuid4().hex[:8]}.png"
        try:
            IMG._save_bytes(data, out)
        except Exception:
            raise HTTPException(400, "That file is not an image")
        return str(out)

    q_img = await save_optional(question_image)
    a_img = await save_optional(answer_image)
    resolved_music = music or config.load_settings().get("default_code_quiz_music", "")

    def work(job):
        job["message"] = "Setting up the quiz…"
        with session() as s:
            p = get_project(s, pid)
            for sc in scenes_of(s, pid):
                for v in variants_of(s, sc.id):
                    s.delete(v)
                # Only ever delete a beat's own per-beat override upload here — never the shared
                # background (global trademark or this request's fresh override), which other
                # beats' rows may also point at.
                if sc.content_image_path:
                    Path(sc.content_image_path).unlink(missing_ok=True)
                if sc.audio_path:
                    Path(sc.audio_path).unlink(missing_ok=True)
                s.delete(sc)
            s.commit()
            specs = [("question", question, q_img), ("wait", "", ""), ("answer", answer, a_img)]
            new_scenes = []
            for i, (beat, text, img) in enumerate(specs):
                sc = Scene(project_id=pid, position=i, narration=text, beat_type=beat,
                           code_text=code if beat in ("question", "answer") else "",
                           show_title_card=show_title_card, content_image_path=img)
                s.add(sc); s.commit(); s.refresh(sc)
                v = ImageVariant(scene_id=sc.id, path=img or str(bg_path), prompt="(code-quiz)",
                                 provider="trademark", uploaded=True)
                s.add(v); s.commit(); s.refresh(v)
                sc.approved_image_id = v.id
                s.add(sc); s.commit()
                new_scenes.append(sc.id)
            p.render_json = json.dumps({**json.loads(p.render_json or "{}"), "music": resolved_music},
                                        ensure_ascii=False)
            p.step = max(p.step, 2)
            touch(p); s.add(p); s.commit()
            prov, voice, rate, pitch = p.tts_provider, p.voice, p.rate, p.pitch

        for i, sid in enumerate(new_scenes):
            with session() as s:
                sc = s.get(Scene, sid)
                text, beat = sc.narration, sc.beat_type
                p = get_project(s, pid)
            job["message"] = f"Recording beat {i + 1}/3…"
            if beat == "wait":
                out = pdir(pid) / "audio" / f"scene_{sid}_wait.wav"
                TTS.make_silence(out, 3.0)
                dur, words = 3.0, []
            else:
                out = pdir(pid) / "audio" / f"scene_{sid}_{uuid.uuid4().hex[:8]}.wav"
                dur, words, used = TTS.synth(text, prov, voice, rate, pitch, out)
                if used != prov:
                    job["warnings"].append(f"{beat}: {prov} failed, used {used} instead")
            with session() as s:
                sc = s.get(Scene, sid)
                sc.audio_path, sc.audio_duration = str(out), dur
                sc.audio_key = audio_key(sc.narration, p)
                sc.words_json = json.dumps(words, ensure_ascii=False)
                s.add(sc); s.commit()
            job["progress"] = (i + 1) / 3

        with session() as s:
            p = get_project(s, pid); p.step = max(p.step, 4); touch(p); s.add(p); s.commit()
        job["message"] = "Code quiz ready"
        return project_view(pid)
    return job_public(start_job(f"code-quiz:{pid}", pid, work))
```

Note the cleanup line inside the scene-replacement loop deletes old per-beat override image files from disk (they live under `pdir(pid)/images/`) but never deletes the trademark file itself (`v.path != str(bg_path)` check) — this is what keeps re-submitting the form (editing a question later) from leaking files, per Review Focus.

- [ ] **Step 3: Verify the happy path**

Run (from `backend/`): `.venv/bin/python -c "
import io, time
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
c.put('/api/settings', json={'tts_provider': 'offline'})
buf = io.BytesIO(); Image.new('RGB', (40, 40), (1,2,3)).save(buf, 'PNG')
c.post('/api/settings/trademark-image', files={'file': ('bg.png', buf.getvalue(), 'image/png')})
pid = c.post('/api/projects', json={'content_type': 'code_quiz', 'language': 'en'}).json()['id']
j = c.post(f'/api/projects/{pid}/code-quiz', data={'question': 'What does this log?\nA) 5\nB) 10', 'code': 'let x = 5', 'answer': 'B) 10'}).json()
while j['status'] == 'running':
    time.sleep(0.2); j = c.get(f\"/api/jobs/{j['id']}\").json()
print(j['status'])
scenes = c.get(f'/api/projects/{pid}').json()['scenes']
print([(s['beat_type'], round(s['audio_duration'], 1)) for s in scenes])
"`
Expected: `done` then `[('question', <a positive number>), ('wait', 3.0), ('answer', <a positive number>)]`

- [ ] **Step 4: Verify the no-trademark-image error path**

Run: `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
from app.config import TRADEMARK_IMAGE
TRADEMARK_IMAGE.unlink(missing_ok=True)
c = TestClient(app)
pid = c.post('/api/projects', json={'content_type': 'code_quiz', 'language': 'en'}).json()['id']
r = c.post(f'/api/projects/{pid}/code-quiz', data={'question': 'q', 'answer': 'a'})
print(r.status_code, r.json())
"`
Expected: `400 {'detail': 'Set a trademark background image in Settings first, or upload one for this video'}`
(Re-upload a trademark image afterward with Step 3's snippet if you want to keep testing locally — this step intentionally deletes it.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: add POST /api/projects/{pid}/code-quiz endpoint"
```

---

### Task 7: Expose beat fields from `project_view`

**Files:**
- Modify: `backend/app/main.py:122-149` (`project_view`)

**Interfaces:**
- Consumes: `Scene.beat_type/code_text/show_title_card/content_image_path` (Task 1).
- Produces: each scene in `GET /api/projects/{pid}` gains `beat_type`, `code_text`, `show_title_card`, `content_image_url`.

- [ ] **Step 1: Write a throwaway check**

Run (from `backend/`): `.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
pid = c.post('/api/projects', json={'story': 'x', 'language': 'en'}).json()['id']
print('beat_type' in c.get(f'/api/projects/{pid}').json().get('scenes', [{}])[0] if c.get(f'/api/projects/{pid}').json()['scenes'] else 'no scenes yet')
"`
(No scenes exist yet for a bare project, so this just confirms the endpoint runs — the real check is Step 3.)

- [ ] **Step 2: Implement**

In `backend/app/main.py`, change the `out_scenes.append(...)` block (lines 129-141) from:

```python
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
```

to:

```python
            out_scenes.append({
                "id": sc.id, "position": sc.position, "narration": sc.narration, "image_prompt": sc.image_prompt,
                "caption": sc.caption, "approved_image_id": sc.approved_image_id,
                "beat_type": sc.beat_type, "code_text": sc.code_text, "show_title_card": sc.show_title_card,
                "content_image_url": url_of(sc.content_image_path) if sc.content_image_path else "",
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
```

- [ ] **Step 3: Verify**

Run the Step 3 snippet from Task 6 again, but print the full scene dict for one scene:
`.venv/bin/python -c "
... (same setup as Task 6 Step 3) ...
print(scenes[0]['beat_type'], scenes[0]['show_title_card'], scenes[0]['code_text'])
"`
Expected: `question True let x = 5`

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: expose beat fields from project_view"
```

---

### Task 8: Render — code block + heading PIL overlay

**Files:**
- Modify: `backend/app/pipeline/render.py:1-18` (imports), `backend/app/pipeline/render.py:128-149` (`fit_vertical`)

**Interfaces:**
- Produces: `R.draw_code_block(img: Image.Image, code: str) -> Image.Image`, `R.draw_beat_heading(img: Image.Image, text: str) -> Image.Image`, `R.fit_vertical(src, dst, warm=False, overlay: dict | None = None)` (new optional 4th param; old 3-arg call sites unaffected).
- Consumes: `FONTS` (existing import), `WORK_SCALE`/`OUT_W`/`OUT_H` (existing module constants).

- [ ] **Step 1: Write a throwaway check that overlay helpers don't exist yet**

Run (from `backend/`): `.venv/bin/python -c "from app.pipeline import render as R; R.draw_code_block"`
Expected: `AttributeError`

- [ ] **Step 2: Add imports**

In `backend/app/pipeline/render.py`, change line 14 from:

```python
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
```

to:

```python
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pygments import lex
from pygments.lexers import JavascriptLexer
from pygments.token import Token
```

- [ ] **Step 3: Add the font helpers and overlay functions**

In `backend/app/pipeline/render.py`, add after `warm_grade()` (after line 126, before `def fit_vertical`):

```python
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
```

- [ ] **Step 4: Wire the overlay into `fit_vertical`**

Change the signature and body of `fit_vertical` (`backend/app/pipeline/render.py:128-149`) from:

```python
def fit_vertical(src: Path, dst: Path, warm: bool = False) -> None:
    """Make a 9:16 image at 2x output size: crop if close to 9:16, otherwise blurred-background letterbox."""
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
    if warm:
        img = warm_grade(img)
    img.save(dst, "JPEG", quality=94)
```

to:

```python
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
```

- [ ] **Step 5: Verify**

Run (from `backend/`): `.venv/bin/python -c "
from pathlib import Path
from PIL import Image
from app.pipeline import render as R
src = Path('/tmp/overlay_src.jpg'); dst = Path('/tmp/overlay_dst.jpg')
Image.new('RGB', (1080, 1920), (20, 30, 40)).save(src)
R.fit_vertical(src, dst, overlay={'show_title_card': True, 'title_text': 'What does this log?\nA) 5\nB) 10',
                                   'code_text': 'function f() {\n  return 5\n}'})
print(dst.exists(), Image.open(dst).size)
R.fit_vertical(src, dst)  # old 3-arg call must still work unchanged
print('legacy call ok')
"`
Expected: `True (2160, 3840)` then `legacy call ok`

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline/render.py
git commit -m "feat: add code-block and heading overlay to fit_vertical"
```

---

### Task 9: Render — countdown for the `wait` beat

**Files:**
- Modify: `backend/app/pipeline/render.py:208-251` (`render()`'s `make_clip`)

**Interfaces:**
- Consumes: `draw_beat_heading`/`draw_code_block`/`fit_vertical` overlay param (Task 8), `tl["leads"]` (existing, computed in `render()`).
- Produces: scenes with `beat_type == "wait"` get a 3-2-1 countdown baked into their clip via ffmpeg `drawtext`; scenes without `beat_type` are unaffected (Review Focus: story regression).

- [ ] **Step 1: Write a throwaway check that a plain scene dict renders unaffected today**

Not applicable as a "fails first" check here (this task only adds a new conditional branch) — instead, confirm current behavior before editing: run `backend/.venv/bin/python backend/tests/smoke_test.py` from the repo root and confirm it prints `all good` (baseline, before touching `render()`).

- [ ] **Step 2: Implement**

In `backend/app/pipeline/render.py`, change `make_clip` inside `render()` (lines 233-241) from:

```python
        def make_clip(i: int) -> Path:
            img = work / f"img_{i:02d}.jpg"
            fit_vertical(Path(scenes[i]["image"]), img, bool(opts.get("warm")))
            frames = round(tl["lengths"][i] * FPS)
            clip = work / f"clip_{i:02d}.mp4"
            _run([FFMPEG, "-y", "-v", "error", "-i", str(img), "-vf",
                  _zoompan(motions[i], frames, strength) + ",format=yuv420p",
                  "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(clip)])
            return clip
```

to:

```python
        def make_clip(i: int) -> Path:
            sc = scenes[i]
            img = work / f"img_{i:02d}.jpg"
            overlay = {"show_title_card": sc.get("show_title_card", False), "title_text": sc.get("narration", ""),
                       "code_text": sc.get("code_text", "")} if sc.get("beat_type") else None
            fit_vertical(Path(sc["image"]), img, bool(opts.get("warm")), overlay)
            frames = round(tl["lengths"][i] * FPS)
            clip = work / f"clip_{i:02d}.mp4"
            vf = _zoompan(motions[i], frames, strength) + ",format=yuv420p"
            if sc.get("beat_type") == "wait":
                vf += _countdown_filter(tl["leads"][i])
            _run([FFMPEG, "-y", "-v", "error", "-i", str(img), "-vf", vf,
                  "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(clip)])
            return clip
```

Add the `_countdown_filter` helper just above `def render(` (after `plan_timeline`, before line 208):

```python
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
```

- [ ] **Step 3: Verify**

Run (from `backend/`): `.venv/bin/python -c "
from app.pipeline.render import _countdown_filter
f = _countdown_filter(0.55)
print(f.count('drawtext='), '0.550' in f, '3.550' in f)
"`
Expected: `3 True True`

Then re-run the full smoke test from the repo root: `backend/.venv/bin/python backend/tests/smoke_test.py` — must still print `all good` (confirms the new branch is a true no-op for story scenes, since `sc.get("beat_type")` is `""`/falsy for them).

- [ ] **Step 4: Commit**

```bash
git add backend/app/pipeline/render.py
git commit -m "feat: add 3-2-1 countdown overlay for the code-quiz wait beat"
```

---

### Task 10: Thread beat fields through `render_video`

**Files:**
- Modify: `backend/app/main.py:739-781` (`render_video`)

**Interfaces:**
- Consumes: `Scene.beat_type/code_text/show_title_card` (Task 1), `render()`'s new per-scene dict keys (Task 9).
- Produces: `items` passed into `R.render()` carry `beat_type`, `code_text`, `show_title_card`, `narration`.

- [ ] **Step 1: Write a throwaway check**

Re-run the smoke test baseline (should still pass before this edit, confirming nothing is broken yet): `backend/.venv/bin/python backend/tests/smoke_test.py` → `all good`.

- [ ] **Step 2: Implement**

In `backend/app/main.py`, inside `render_video` (lines 750-759), change:

```python
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
```

to:

```python
        for i, sc in enumerate(scenes_of(s, pid)):
            v = s.get(ImageVariant, sc.approved_image_id) if sc.approved_image_id else None
            if need_images and (not v or not Path(v.path).exists()):
                problems.append(f"scene {i + 1} has no approved image")
            audio_ok = sc.audio_key == self_audio_key(sc.narration) if sc.audio_self else sc.audio_key == audio_key(sc.narration, p)
            if not sc.audio_path or not audio_ok or not Path(sc.audio_path).exists():
                problems.append(f"scene {i + 1} narration is missing or out of date")
            if (v or not need_images) and sc.audio_path:
                items.append({"image": v.path if v else "", "audio": sc.audio_path, "duration": sc.audio_duration,
                              "words": json.loads(sc.words_json or "[]"),
                              "beat_type": sc.beat_type, "code_text": sc.code_text,
                              "show_title_card": sc.show_title_card, "narration": sc.narration})
```

- [ ] **Step 3: Verify**

Run the full code-quiz happy path end-to-end including render (extends Task 6 Step 3 — from `backend/`):

```
.venv/bin/python -c "
import io, time
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
c.put('/api/settings', json={'tts_provider': 'offline'})
buf = io.BytesIO(); Image.new('RGB', (600, 1000), (1,2,3)).save(buf, 'PNG')
c.post('/api/settings/trademark-image', files={'file': ('bg.png', buf.getvalue(), 'image/png')})
pid = c.post('/api/projects', json={'content_type': 'code_quiz', 'language': 'en'}).json()['id']
def wait(j):
    while j['status'] == 'running':
        time.sleep(0.2); j = c.get(f\"/api/jobs/{j['id']}\").json()
    assert j['status'] == 'done', j
    return j
wait(c.post(f'/api/projects/{pid}/code-quiz', data={'question': 'What does this log?\nA) 5\nB) 10', 'code': 'let x = 5', 'answer': 'B) 10'}).json())
j = wait(c.post(f'/api/projects/{pid}/render', json={}).json())
print(j['result'])
"
```
Expected: no exceptions, prints something like `{'url': '/files/...', 'duration': <~6-7 seconds>}` (question-audio + 3.0s wait + answer-audio, plus the usual lead/tail padding).

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: pass beat fields from scenes into the render pipeline"
```

---

### Task 11: Declare the Pygments dependency

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add it**

Current content:
```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlmodel>=0.0.22
httpx>=0.27
pillow>=10.4
python-multipart>=0.0.9
edge-tts>=7.0
```

New content:
```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlmodel>=0.0.22
httpx>=0.27
pillow>=10.4
python-multipart>=0.0.9
edge-tts>=7.0
pygments>=2.17
```

- [ ] **Step 2: Verify it's already satisfied in the venv (no reinstall needed, just confirming the pin is sane)**

Run: `backend/.venv/bin/python -c "import pygments; print(pygments.__version__)"` → should print `2.21.0` (already installed per earlier check) — confirms `>=2.17` is satisfied.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: declare pygments dependency used by the code-quiz render overlay"
```

---

### Task 12: Permanent regression test — full code-quiz flow

**Files:**
- Modify: `backend/tests/smoke_test.py`

**Interfaces:**
- Consumes: everything from Tasks 1-10.

- [ ] **Step 1: Write the test**

In `backend/tests/smoke_test.py`, add the import `io` at the top (after `import json` on line 5):

```python
import io
```

and `from PIL import Image` after the existing imports (after line 11, `from pathlib import Path`):

```python
from PIL import Image
```

Add `from app.config import TRADEMARK_IMAGE` next to the existing `from app.config import FFMPEG, FFPROBE, PROJECTS` line (line 18) — change it to:

```python
from app.config import FFMPEG, FFPROBE, PROJECTS, TRADEMARK_IMAGE  # noqa: E402
```

Add the new test function after `test_self_recording_voice_change()` (after line 120, before the `if __name__ == "__main__":` block):

```python
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

    assert not old_question_image.exists(), "replaced per-beat override image must be cleaned up from disk"
    print("[code-quiz] OK  replaced per-beat override image is deleted from disk")

    assert TRADEMARK_IMAGE.exists()  # the shared trademark file itself must survive scene replacement
    print("[code-quiz] OK  trademark image untouched by scene replacement")
```

Change the `if __name__ == "__main__":` block (lines 123-127) from:

```python
if __name__ == "__main__":
    run("hi")
    run("en")
    test_self_recording_voice_change()
    print("all good")
```

to:

```python
if __name__ == "__main__":
    run("hi")
    run("en")
    test_self_recording_voice_change()
    test_code_quiz_flow()
    print("all good")
```

- [ ] **Step 2: Run it and confirm it passes**

Run from the repo root: `backend/.venv/bin/python backend/tests/smoke_test.py`
Expected: prints the `[hi] OK`/`[en] OK`/`[voice] OK` lines as before, then the three new `[code-quiz] OK` lines, then `all good`. Any failure here is a real regression or bug — fix the root cause in the task it belongs to (Tasks 1-10), don't patch the test to tolerate it.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke_test.py
git commit -m "test: add end-to-end code-quiz flow coverage to smoke_test.py"
```

---

### Task 13: Frontend — Settings page: trademark image + default music

**Files:**
- Modify: `frontend/src/Settings.jsx`

**Interfaces:**
- Consumes: `POST /api/settings/trademark-image` (Task 5), `GET /api/catalog` → `trademark_url` (Task 5), `GET /api/music`/`POST /api/music/upload` (existing), `DEFAULTS.default_code_quiz_music` (Task 2, via the generic `/api/settings` GET/PUT).

- [ ] **Step 1: Add state and loaders**

In `frontend/src/Settings.jsx`, change the top of `Settings()` (lines 53-58) from:

```jsx
export default function Settings({ health }) {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState([]);

  useEffect(() => { api.get("/api/settings").then(setS); }, []);
  if (!s) return <p className="text-mist">Loading settings…</p>;
```

to:

```jsx
export default function Settings({ health }) {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState([]);
  const [trademarkUrl, setTrademarkUrl] = useState("");
  const [tracks, setTracks] = useState([]);
  const trademarkRef = useRef();

  useEffect(() => {
    api.get("/api/settings").then(setS);
    api.get("/api/catalog").then((c) => setTrademarkUrl(c.trademark_url));
    api.get("/api/music").then((m) => setTracks(m.tracks));
  }, []);
  if (!s) return <p className="text-mist">Loading settings…</p>;

  const uploadTrademark = async (file) => {
    const r = await api.upload("/api/settings/trademark-image", file);
    setTrademarkUrl(r.trademark_url);
  };
```

Add `useRef` to the React import at the top of the file (line 1), changing:

```jsx
import { useEffect, useState } from "react";
```

to:

```jsx
import { useEffect, useRef, useState } from "react";
```

- [ ] **Step 2: Add the Section**

In `frontend/src/Settings.jsx`, add a new `Section` right after the existing `Section title="Voice"` block (after line 131, before `<Section title="Reliability">` on line 133):

```jsx
      <Section title="Code quiz reels" hint="Your trademark background image and default music, reused automatically for every code-quiz video.">
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <Label>Trademark background image</Label>
            {trademarkUrl ? (
              <img src={trademarkUrl} alt="" className="mb-2 h-40 w-auto rounded-lg border border-line object-cover" />
            ) : (
              <p className="mb-2 text-sm text-mist">None set yet — code-quiz videos need one.</p>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => trademarkRef.current.click()}>
              {trademarkUrl ? "Replace image" : "Upload image"}
            </button>
            <input ref={trademarkRef} type="file" accept="image/*" hidden
              onChange={(e) => e.target.files[0] && uploadTrademark(e.target.files[0])} />
          </div>
          <div>
            <Label htmlFor="dcqm">Default music track</Label>
            <select id="dcqm" className="field" value={s.default_code_quiz_music}
              onChange={(e) => set("default_code_quiz_music", e.target.value)}>
              <option value="">No music</option>
              {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
            <p className="mt-1 text-xs text-mist">Add tracks from the Video step of any story project — they're shared across the app.</p>
          </div>
        </div>
      </Section>
```

- [ ] **Step 3: Verify in the browser**

Start the dev server (or the built app), open Settings, confirm: no trademark image shows "None set yet…"; uploading an image shows the preview and persists across a page reload; the default-music dropdown lists existing tracks from `music/` and saving persists the choice (reload Settings and confirm the selection sticks after clicking "Save settings").

- [ ] **Step 4: Commit**

```bash
git add frontend/src/Settings.jsx
git commit -m "feat: add trademark image and default music to Settings page"
```

---

### Task 14: Frontend — Home.jsx content-type choice + code-quiz creation

**Files:**
- Modify: `frontend/src/Home.jsx`

**Interfaces:**
- Consumes: `POST /api/projects` with `content_type` (Task 4), `POST /api/projects/{pid}/code-quiz` (Task 6).

- [ ] **Step 1: Add state and a content-type toggle**

In `frontend/src/Home.jsx`, change the top of `Home()` (lines 39-49) from:

```jsx
export default function Home() {
  const [projects, setProjects] = useState(null);
  const [styles, setStyles] = useState([]);
  const [story, setStory] = useState("");
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("hi");
  const [styleKey, setStyleKey] = useState("cinematic");
  const [custom, setCustom] = useState("");
  const [seconds, setSeconds] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
```

to:

```jsx
export default function Home() {
  const [projects, setProjects] = useState(null);
  const [styles, setStyles] = useState([]);
  const [contentType, setContentType] = useState("story");
  const [story, setStory] = useState("");
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("hi");
  const [styleKey, setStyleKey] = useState("cinematic");
  const [custom, setCustom] = useState("");
  const [seconds, setSeconds] = useState(60);
  const [question, setQuestion] = useState("");
  const [code, setCode] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
```

- [ ] **Step 2: Branch the create handler**

Change `create` (lines 57-68) from:

```jsx
  const create = async () => {
    setBusy(true);
    setErr("");
    try {
      const { id } = await api.post("/api/projects", { title, story, language, style_key: styleKey, style_custom: custom, target_seconds: seconds });
      await api.post(`/api/projects/${id}/script`);
      window.location.hash = `#/p/${id}`;
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };
```

to:

```jsx
  const create = async () => {
    setBusy(true);
    setErr("");
    try {
      if (contentType === "code_quiz") {
        const { id } = await api.post("/api/projects", { title, language, content_type: "code_quiz" });
        await api.postForm(`/api/projects/${id}/code-quiz`, { question, code, answer });
        window.location.hash = `#/p/${id}`;
      } else {
        const { id } = await api.post("/api/projects", { title, story, language, style_key: styleKey, style_custom: custom, target_seconds: seconds });
        await api.post(`/api/projects/${id}/script`);
        window.location.hash = `#/p/${id}`;
      }
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };
```

- [ ] **Step 3: Add the toggle and the code-quiz form**

Change the opening of the create-form `<div>` (lines 89-100) from:

```jsx
        <div className="mt-6 space-y-5 rounded-2xl border border-line bg-dusk/70 p-5 md:p-6">
          <div>
            <Label htmlFor="story">Your story</Label>
            <textarea
              id="story"
              className="field min-h-56"
              value={story}
              onChange={(e) => setStory(e.target.value)}
              placeholder="एक बार माता पार्वती ने अपने उबटन से एक बालक बनाया… (Hindi or English — the narration language is chosen below)"
            />
            <p className="mt-1 text-xs text-mist">{words} words. The script will be condensed to fit the length you pick</p>
          </div>
```

to:

```jsx
        <div className="mt-6 space-y-5 rounded-2xl border border-line bg-dusk/70 p-5 md:p-6">
          <div>
            <Label>What are you making?</Label>
            <Segmented name="Content type" value={contentType} onChange={setContentType}
              options={[{ value: "story", label: "Mythology story" }, { value: "code_quiz", label: "Code quiz" }]} />
          </div>
          {contentType === "story" ? (
          <div>
            <Label htmlFor="story">Your story</Label>
            <textarea
              id="story"
              className="field min-h-56"
              value={story}
              onChange={(e) => setStory(e.target.value)}
              placeholder="एक बार माता पार्वती ने अपने उबटन से एक बालक बनाया… (Hindi or English — the narration language is chosen below)"
            />
            <p className="mt-1 text-xs text-mist">{words} words. The script will be condensed to fit the length you pick</p>
          </div>
          ) : (
          <div className="space-y-4">
            <div>
              <Label htmlFor="question">Question (one option per line, if any)</Label>
              <textarea id="question" className="field min-h-32" value={question} onChange={(e) => setQuestion(e.target.value)}
                placeholder={"What does this log?\nA) 5\nB) 10\nC) 15\nD) 20"} />
            </div>
            <div>
              <Label htmlFor="code">Code snippet (optional)</Label>
              <textarea id="code" className="field min-h-32 font-mono text-sm" value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="answer">Answer + explanation</Label>
              <textarea id="answer" className="field min-h-32" value={answer} onChange={(e) => setAnswer(e.target.value)} />
            </div>
          </div>
          )}
```

Now gate the language/length/style/title fields and the submit button so they only show the parts relevant to `contentType`. Change the block starting `<div className="grid gap-5 sm:grid-cols-2">` (lines 101-111) by wrapping it (and the `Art style` block right after it, lines 112-115) in a story-only conditional, and adjust the submit button's disabled condition. Replace lines 101-124:

```jsx
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <Label>Narration language</Label>
              <Segmented name="Narration language" value={language} onChange={setLanguage}
                options={[{ value: "hi", label: "हिन्दी" }, { value: "en", label: "English (Indian)" }]} />
            </div>
            <div>
              <Label>Video length</Label>
              <Segmented name="Video length" value={seconds} onChange={setSeconds} options={LENGTHS} />
            </div>
          </div>
          <div>
            <Label>Art style for this story</Label>
            <StyleFields {...{ styleKey, setStyleKey, custom, setCustom, styles }} />
          </div>
          <div>
            <Label htmlFor="title">Title (optional)</Label>
            <input id="title" className="field" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Leave empty and one will be suggested" />
          </div>
          {err && <p role="alert" className="text-sm text-sindoor">{err}</p>}
          <button className="btn btn-primary" disabled={busy || words < 15} onClick={create}>
            {busy ? "Starting…" : "Write the scene script"}
          </button>
          {words > 0 && words < 15 && <span className="ml-3 text-sm text-mist">Add a little more story first.</span>}
```

with:

```jsx
          {contentType === "story" ? (
          <>
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <Label>Narration language</Label>
              <Segmented name="Narration language" value={language} onChange={setLanguage}
                options={[{ value: "hi", label: "हिन्दी" }, { value: "en", label: "English (Indian)" }]} />
            </div>
            <div>
              <Label>Video length</Label>
              <Segmented name="Video length" value={seconds} onChange={setSeconds} options={LENGTHS} />
            </div>
          </div>
          <div>
            <Label>Art style for this story</Label>
            <StyleFields {...{ styleKey, setStyleKey, custom, setCustom, styles }} />
          </div>
          </>
          ) : (
          <div>
            <Label>Narration language</Label>
            <Segmented name="Narration language" value={language} onChange={setLanguage}
              options={[{ value: "hi", label: "हिन्दी" }, { value: "en", label: "English (Indian)" }]} />
          </div>
          )}
          <div>
            <Label htmlFor="title">Title</Label>
            <input id="title" className="field" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder={contentType === "story" ? "Leave empty and one will be suggested" : "e.g. JS Closures Quiz #1"} />
          </div>
          {err && <p role="alert" className="text-sm text-sindoor">{err}</p>}
          <button className="btn btn-primary"
            disabled={busy || (contentType === "story" ? words < 15 : !question.trim() || !answer.trim())}
            onClick={create}>
            {busy ? "Starting…" : contentType === "story" ? "Write the scene script" : "Create the quiz"}
          </button>
          {contentType === "story" && words > 0 && words < 15 && <span className="ml-3 text-sm text-mist">Add a little more story first.</span>}
```

- [ ] **Step 4: Verify in the browser**

Start the dev server, open Home, switch the toggle to "Code quiz", fill in question/answer, submit — confirm it navigates to the new project's Studio view without errors (it will land on a step; Task 15 makes that step render correctly). Switch back to "Mythology story" and confirm the original creation flow still works exactly as before.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/Home.jsx
git commit -m "feat: add code-quiz content type to project creation"
```

---

### Task 15: Frontend — Studio.jsx wiring + CodeQuizStep edit form

**Files:**
- Create: `frontend/src/steps/CodeQuizStep.jsx`
- Modify: `frontend/src/Studio.jsx`

**Interfaces:**
- Consumes: `POST /api/projects/{pid}/code-quiz` (Task 6), `project_view` beat fields (Task 7).
- Produces: `CodeQuizStep` component, used as Studio's step-1 view for `content_type === "code_quiz"` projects.

- [ ] **Step 1: Create `CodeQuizStep.jsx`**

```jsx
import { useEffect, useRef, useState } from "react";
import { api, useJob } from "../api";
import { Label, Progress, Section, Toggle } from "../components/ui";

export default function CodeQuizStep({ p, reload, go }) {
  const question = p.scenes.find((s) => s.beat_type === "question");
  const answer = p.scenes.find((s) => s.beat_type === "answer");
  const [q, setQ] = useState(question?.narration || "");
  const [code, setCode] = useState(question?.code_text || "");
  const [a, setA] = useState(answer?.narration || "");
  const [showTitleCard, setShowTitleCard] = useState(question?.show_title_card ?? true);
  const [music, setMusic] = useState(p.render?.music || "");
  const [tracks, setTracks] = useState([]);
  const [questionImage, setQuestionImage] = useState(null);
  const [answerImage, setAnswerImage] = useState(null);
  const [backgroundImage, setBackgroundImage] = useState(null);
  const qImgRef = useRef(); const aImgRef = useRef(); const bgImgRef = useRef();
  const [job, start] = useJob(async (j) => { if (j.status === "done") { await reload(); go(4); } });
  const running = job?.status === "running";

  useEffect(() => { api.get("/api/music").then((m) => setTracks(m.tracks)); }, []);

  const save = () => start(api.postForm(`/api/projects/${p.id}/code-quiz`, {
    question: q, code, answer: a, show_title_card: showTitleCard, music,
    ...(questionImage ? { question_image: questionImage } : {}),
    ...(answerImage ? { answer_image: answerImage } : {}),
    ...(backgroundImage ? { background_image: backgroundImage } : {}),
  }));

  return (
    <Section title="Code quiz" hint="This is used exactly as written — no AI rewrites it. Line breaks are kept, so put each multiple-choice option on its own line.">
      <div className="space-y-5">
        <div>
          <Label htmlFor="cq-question">Question</Label>
          <textarea id="cq-question" className="field min-h-32" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="mt-2 flex items-center gap-2">
            {question?.content_image_url && <img src={question.content_image_url} alt="" className="h-12 w-12 rounded border border-line object-cover" />}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => qImgRef.current.click()}>
              {questionImage ? questionImage.name : "Image instead of text (e.g. math)"}
            </button>
            <input ref={qImgRef} type="file" accept="image/*" hidden onChange={(e) => setQuestionImage(e.target.files[0] || null)} />
          </div>
        </div>
        <div>
          <Label htmlFor="cq-code">Code snippet (optional)</Label>
          <textarea id="cq-code" className="field min-h-32 font-mono text-sm" value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="cq-answer">Answer + explanation</Label>
          <textarea id="cq-answer" className="field min-h-32" value={a} onChange={(e) => setA(e.target.value)} />
          <div className="mt-2 flex items-center gap-2">
            {answer?.content_image_url && <img src={answer.content_image_url} alt="" className="h-12 w-12 rounded border border-line object-cover" />}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => aImgRef.current.click()}>
              {answerImage ? answerImage.name : "Image instead of text (e.g. math)"}
            </button>
            <input ref={aImgRef} type="file" accept="image/*" hidden onChange={(e) => setAnswerImage(e.target.files[0] || null)} />
          </div>
        </div>
        <Toggle label="Show question/answer text on screen" checked={showTitleCard} onChange={setShowTitleCard} />
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="cq-music">Music for this video</Label>
            <select id="cq-music" className="field" value={music} onChange={(e) => setMusic(e.target.value)}>
              <option value="">Use the default from Settings</option>
              {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <Label>Background image for this video</Label>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => bgImgRef.current.click()}>
              {backgroundImage ? backgroundImage.name : "Use a different image just for this video"}
            </button>
            <input ref={bgImgRef} type="file" accept="image/*" hidden onChange={(e) => setBackgroundImage(e.target.files[0] || null)} />
          </div>
        </div>
        <button className="btn btn-primary" disabled={running || !q.trim() || !a.trim()} onClick={save}>
          {running ? "Saving…" : question ? "Save changes" : "Create the quiz"}
        </button>
        <Progress job={job} />
      </div>
    </Section>
  );
}
```

(The music `<select>`'s default empty value means "use Settings' default code-quiz track" — this matches `set_code_quiz`'s `resolved_music = music or config.load_settings().get("default_code_quiz_music", "")` from Task 6. Picking "No music" explicitly isn't offered here deliberately — the spec's default-with-override model doesn't call for a video with no music at all; if that's ever wanted, it's a one-line addition of an empty-string option later.)

- [ ] **Step 2: Wire it into Studio.jsx**

In `frontend/src/Studio.jsx`, add the import (after line 3, `import StoryStep from "./steps/StoryStep";`):

```jsx
import CodeQuizStep from "./steps/CodeQuizStep";
```

Change the `STEPS` constant (lines 9-15) and `readiness()` (lines 17-26) from:

```jsx
const STEPS = [
  { n: 1, name: "Story", sub: "Text, language, style" },
  { n: 2, name: "Scenes", sub: "Review the script" },
  { n: 3, name: "Images", sub: "Approve each picture" },
  { n: 4, name: "Voice", sub: "Pick the narrator" },
  { n: 5, name: "Video", sub: "Render & download" },
];

export function readiness(p) {
  const sc = p?.scenes || [];
  return {
    1: true,
    2: sc.length > 0,
    3: sc.length > 0,
    4: sc.length > 0 && sc.every((s) => s.approved_image_id),
    5: sc.length > 0 && sc.every((s) => s.approved_image_id && s.audio_current),
  };
}
```

to:

```jsx
const STORY_STEPS = [
  { n: 1, name: "Story", sub: "Text, language, style" },
  { n: 2, name: "Scenes", sub: "Review the script" },
  { n: 3, name: "Images", sub: "Approve each picture" },
  { n: 4, name: "Voice", sub: "Pick the narrator" },
  { n: 5, name: "Video", sub: "Render & download" },
];

const CODE_QUIZ_STEPS = [
  { n: 1, name: "Quiz", sub: "Question, code, answer" },
  { n: 4, name: "Voice", sub: "Pick the narrator" },
  { n: 5, name: "Video", sub: "Render & download" },
];

export function readiness(p) {
  const sc = p?.scenes || [];
  if (p?.content_type === "code_quiz") {
    return { 1: true, 4: sc.length > 0, 5: sc.length > 0 && sc.every((s) => s.audio_current) };
  }
  return {
    1: true,
    2: sc.length > 0,
    3: sc.length > 0,
    4: sc.length > 0 && sc.every((s) => s.approved_image_id),
    5: sc.length > 0 && sc.every((s) => s.approved_image_id && s.audio_current),
  };
}
```

Change the body of `Studio()` where `STEPS.map` and the step-rendering switch live (lines 79-112). First, inside the function body, add right after `const ready = readiness(p);` (line 58):

```jsx
  const STEPS = p.content_type === "code_quiz" ? CODE_QUIZ_STEPS : STORY_STEPS;
```

Then change the step-rendering switch (lines 106-112) from:

```jsx
        {step === 1 && <StoryStep {...props} />}
        {step === 2 && <ScriptStep {...props} />}
        {step === 3 && <ImagesStep {...props} />}
        {step === 4 && <VoiceStep {...props} />}
        {step === 5 && <RenderStep {...props} />}
```

to:

```jsx
        {step === 1 && (p.content_type === "code_quiz" ? <CodeQuizStep {...props} /> : <StoryStep {...props} />)}
        {step === 2 && <ScriptStep {...props} />}
        {step === 3 && <ImagesStep {...props} />}
        {step === 4 && <VoiceStep {...props} />}
        {step === 5 && <RenderStep {...props} />}
```

- [ ] **Step 3: Verify in the browser**

Create a code-quiz project via Home (Task 14). Confirm Studio's sidebar shows only "Quiz / Voice / Video" (no "Scenes"/"Images"). Edit the question/answer on the Quiz step and save — confirm it re-synthesizes narration and the Voice step reflects the new audio. Render the video from the Video step and confirm it plays with the countdown and code overlay visible. Then open an existing *story* project and confirm its sidebar still shows all 5 steps exactly as before (regression check).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/steps/CodeQuizStep.jsx frontend/src/Studio.jsx
git commit -m "feat: add CodeQuizStep and wire content-type-aware wizard steps"
```

---

### Task 16: Frontend — skip the LLM metadata step for code-quiz projects

**Why this task exists:** `RenderStep.jsx` (reused unchanged by Task 15 for the Video step of both content types) auto-fires `POST /api/projects/{pid}/metadata` on mount whenever `p.metadata?.title` is empty, and also renders a "Title, description and hashtags" section built around that LLM call. Left as-is, opening the Video step of a fresh code-quiz project would silently make an LLM call — violating the Global Constraint that no LLM is involved anywhere in the code-quiz content path (spec §4.3: "Skipped for `code_quiz` projects").

**Files:**
- Modify: `frontend/src/steps/RenderStep.jsx:22-33` (mount effect), `frontend/src/steps/RenderStep.jsx:148-165` (metadata Section)

- [ ] **Step 1: Write a throwaway check that the effect currently fires unconditionally**

Read `frontend/src/steps/RenderStep.jsx:31`: `if (!p.metadata?.title) mstart(api.post(...))` — confirms there's no `content_type` guard today.

- [ ] **Step 2: Guard the mount effect**

Change line 31 from:

```jsx
    if (!p.metadata?.title) mstart(api.post(`/api/projects/${p.id}/metadata`));
```

to:

```jsx
    if (p.content_type !== "code_quiz" && !p.metadata?.title) mstart(api.post(`/api/projects/${p.id}/metadata`));
```

- [ ] **Step 3: Hide the metadata Section for code-quiz projects**

Change the metadata `Section` block (lines 148-165) from:

```jsx
        <Section title="Title, description and hashtags" hint="Suggested for the upload; edit anything."
          actions={<button className="btn btn-ghost btn-sm" disabled={mjob?.status === "running"} onClick={() => mstart(api.post(`/api/projects/${p.id}/metadata`))}>Suggest again</button>}>
          {mjob?.status === "running" && <Progress job={mjob} />}
          <div className="space-y-3">
            {[["title", "Title", 1], ["description", "Description", 4]].map(([k, l, rows]) => (
              <div key={k}>
                <div className="flex items-center justify-between"><Label>{l}</Label>
                  <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy(k, meta[k] || "")}>{copied === k ? "Copied" : "Copy"}</button></div>
                <textarea className="field text-sm" rows={rows} lang={p.language} value={meta[k] || ""} onChange={(e) => saveMeta({ ...meta, [k]: e.target.value })} />
              </div>
            ))}
            <div>
              <div className="flex items-center justify-between"><Label>Hashtags</Label>
                <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy("tags", tags)}>{copied === "tags" ? "Copied" : "Copy"}</button></div>
              <textarea className="field text-sm" rows={2} value={tags} onChange={(e) => saveMeta({ ...meta, hashtags: e.target.value.split(/\s+/).filter(Boolean) })} />
            </div>
          </div>
        </Section>
```

to:

```jsx
        {p.content_type === "code_quiz" ? (
        <Section title="Title" hint="Set on the Quiz step's project title — shown on screen at the start of the video.">
          <p className="text-sm text-mist">{p.title}</p>
        </Section>
        ) : (
        <Section title="Title, description and hashtags" hint="Suggested for the upload; edit anything."
          actions={<button className="btn btn-ghost btn-sm" disabled={mjob?.status === "running"} onClick={() => mstart(api.post(`/api/projects/${p.id}/metadata`))}>Suggest again</button>}>
          {mjob?.status === "running" && <Progress job={mjob} />}
          <div className="space-y-3">
            {[["title", "Title", 1], ["description", "Description", 4]].map(([k, l, rows]) => (
              <div key={k}>
                <div className="flex items-center justify-between"><Label>{l}</Label>
                  <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy(k, meta[k] || "")}>{copied === k ? "Copied" : "Copy"}</button></div>
                <textarea className="field text-sm" rows={rows} lang={p.language} value={meta[k] || ""} onChange={(e) => saveMeta({ ...meta, [k]: e.target.value })} />
              </div>
            ))}
            <div>
              <div className="flex items-center justify-between"><Label>Hashtags</Label>
                <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy("tags", tags)}>{copied === "tags" ? "Copied" : "Copy"}</button></div>
              <textarea className="field text-sm" rows={2} value={tags} onChange={(e) => saveMeta({ ...meta, hashtags: e.target.value.split(/\s+/).filter(Boolean) })} />
            </div>
          </div>
        </Section>
        )}
```

- [ ] **Step 4: Verify in the browser**

Open the Video step of a code-quiz project: confirm no network call to `/metadata` fires (check the browser's network tab) and the "Title" section just shows the project's title as plain text. Open the Video step of an existing story project and confirm the LLM-suggested title/description/hashtags section still behaves exactly as before.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/steps/RenderStep.jsx
git commit -m "fix: skip the LLM metadata step for code-quiz projects"
```

---

### Task 17: Rebuild the frontend and restart the backend

**Files:** none (build/deploy step)

- [ ] **Step 1: Build**

Run from `frontend/`: `npm run build`
Expected: build succeeds, `frontend/dist` is refreshed.

- [ ] **Step 2: Restart the backend so it picks up the new routes**

The backend has no `--reload` (per `start.sh`), so code changes need a manual restart:
```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <pid from above>
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/katha-studio-backend.log 2>&1 & disown
```

- [ ] **Step 3: Verify**

`curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/projects/999999/code-quiz` → expect `404` (project not found — proves the route exists and is live, not the 405 "Method Not Allowed" that a stale process would return).

- [ ] **Step 4: Manual smoke test in the browser**

Go to Settings, upload a trademark image, pick/upload a default music track. Go to Home, create a Code Quiz project end to end (question with options, a code snippet, an answer), render it, and watch the result: trademark background throughout, code block visible during question and answer, 3-2-1 countdown with music swelling during the wait beat, title (from the Title field) shown at the very start. No commit for this step — it's verification only.
