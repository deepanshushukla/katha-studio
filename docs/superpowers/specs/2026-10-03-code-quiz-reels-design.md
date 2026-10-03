# Code Quiz Reels — Design

Date: 2026-10-03
Status: Approved by user, pending implementation plan

## 1. Purpose

Katha Studio currently produces narrated mythology reels: a story prompt goes
through an LLM script step, per-scene AI image generation, TTS narration, and
a render pipeline that composites images + captions + music.

The user wants a second, personal-channel content type: short coding-quiz
reels (JS/Node.js, 15–60s) structured as **question → wait (3-2-1 countdown)
→ answer/explanation**, with a consistent "trademark" background image across
every video, dramatic background music, and the code snippet visible as an
on-screen overlay throughout.

Unlike the mythology flow, the user writes the exact question, code, and
answer themselves — no LLM is involved in generating or rephrasing content
for this type. The LLM remains used only for the existing "story" content
type; it is not touched by this feature.

## 2. Scope

In scope:
- A new `content_type` on `Project`: `"story"` (existing, unchanged) or
  `"code_quiz"` (new).
- A single question → wait → answer structure per video (exactly 3 beats).
  The data model must not preclude adding multiple question/wait/answer
  groups per video later (quick-fire mode), but quick-fire itself is **not**
  built now.
- A fixed trademark background image and a default dramatic music track,
  each set once in Settings and auto-applied to every `code_quiz` project,
  with the ability to override either per project.
- A per-beat optional image upload (question and/or answer) for content that
  plain text can't represent well (math notation, diagrams) — this replaces
  the text title card for that beat when provided.
- A render-time overlay layer: code block, title card text, and the 3-2-1
  countdown, driven entirely by per-scene fields.
- Frontend: a content-type choice at project creation, a Code Quiz input
  form, and two new Settings fields.
- A new offline smoke test covering the full flow.

Out of scope (explicitly deferred):
- Quick-fire (multiple question/answer groups in one reel).
- In-app LaTeX/math rendering — users upload a ready-made image instead.
- Any LLM involvement in code-quiz content generation.
- Per-project art style selection (there is none — the trademark image is
  the only visual, aside from optional per-beat image overrides).
- Configurable countdown duration/style, configurable code-block styling —
  ship with one fixed look; revisit after real-world use.

## 3. Data model changes

`backend/app/models.py`:

- `Project.content_type: str = "story"` — `"story"` or `"code_quiz"`.
- `Project.title: str = ""` — for `code_quiz` projects, this is the exact
  text the user typed; it is both shown on screen (small heading above the
  question) and reused directly as the video's title/caption for sharing.
  The existing LLM-driven `/api/projects/{pid}/metadata` step (title +
  hashtags) is not called for `code_quiz` projects.
- `Scene` gains:
  - `beat_type: str = ""` — `""` (story scenes, unaffected), `"question"`,
    `"wait"`, or `"answer"`.
  - `code_text: str = ""` — the code snippet to overlay, blank if none.
  - `show_title_card: bool = True` — per-project toggle applied to all its
    scenes at creation time; off hides the text title card (code overlay and
    narration/captions are unaffected).
  - `content_image_path: str = ""` — optional per-beat image override. When
    set, it is composited in place of the formatted text title card for that
    beat. Only meaningful for `question`/`answer` beats.
- `Settings` gains:
  - `trademark_background_path: str = ""`
  - `default_code_quiz_music_path: str = ""`

Migration: follow the existing `PRAGMA table_info` + `ALTER TABLE ... ADD
COLUMN` pattern already used in `init_db()` for each new column, with the
defaults above.

Beats are plain ordered `Scene` rows (existing `Scene.order`/position field),
not a new table — this keeps reusing the existing per-scene image/audio/
render iteration instead of teaching the pipeline a second model. Future
quick-fire mode would just mean more groups of 3 scenes in sequence; no
schema change needed for that later.

## 4. Backend flow

### 4.1 Creating a code-quiz project

New endpoint: `POST /api/projects/{pid}/code-quiz`

Request is `multipart/form-data` (text fields plus optional file uploads),
following the existing pattern used by `/api/scenes/{sid}/record-audio` and
the music-upload endpoint — not plain JSON, since `question_image`,
`answer_image`, `background_image`, and `music` are optional file uploads:

```
title=JS Closures Quiz #1
question=What does this log?\nA) 5\nB) 10\nC) 15\nD) 20
code=function counter() { ... }
answer=The answer is B) 10, because...
show_title_card=true
question_image=<file, optional>
answer_image=<file, optional>
background_image=<file, optional>
music=<file, optional>
```

Behavior:
1. Require `sc.project.content_type == "code_quiz"`.
2. Resolve the background image: per-request `background_image` if given,
   else `Settings.trademark_background_path`. If neither exists, return
   `400` with a clear message to set a trademark image in Settings first.
3. Resolve the music track the same way, falling back to
   `Settings.default_code_quiz_music_path`. Missing-with-no-default is **not**
   an error — `code_quiz` projects can render without music, same as story
   projects today.
4. Create exactly 3 `Scene` rows in order:
   - `question`: `narration = question` (verbatim), `code_text = code`,
     `content_image_path = question_image or ""`.
   - `wait`: `narration = ""`, no code/image fields.
   - `answer`: `narration = answer` (verbatim), `code_text = code` (carried
     over so the snippet stays visible), `content_image_path = answer_image
     or ""`.
   - All three get `show_title_card` from the request and the resolved
     background image as their sole, auto-approved `ImageVariant`
     (`provider="trademark"`, skips the AI image provider entirely).
5. Kick off narration synthesis as a job (reuses the existing TTS job
   pattern):
   - `question`/`answer` scenes: existing `synth()`/TTS path, feeding the
     verbatim text directly — no LLM rephrasing step.
   - `wait` scene: generate a fixed ~3.0s silent WAV (new small helper in
     `tts.py`, analogous to the existing offline beep generator) instead of
     calling TTS. `audio_duration = 3.0`.
6. Existing Voice step (pace/pitch/self-record/change-voice) and Music step
   work unchanged on top of these scenes — they already operate per-scene
   and don't know or care that the scenes came from a form instead of the
   LLM script step.

### 4.2 Images step

Skipped entirely for `code_quiz` projects — there is no
`/api/projects/{pid}/images` call in this flow. The one auto-approved
`ImageVariant` per scene (trademark image, or the per-beat
`content_image_path` override composited at render time — see §5) is all
that's needed.

### 4.3 Metadata step

Skipped for `code_quiz` projects. `Project.title` (user-typed) is used
directly wherever the UI/export today shows the LLM-generated title.

## 5. Render overlay

One new compositing step in `backend/app/pipeline/render.py`, applied per
scene in addition to the existing image/caption/music compositing:

- If `scene.content_image_path` is set: composite that image as the main
  content area (replaces the text title card for that beat).
- Else if `scene.show_title_card` and `scene.narration`: draw the narration
  text as a formatted title card — question/answer text rendered as
  multi-line (one line per input line, so "A) 5" etc. stay on their own
  line), not reflowed into a paragraph.
- If `scene.code_text`: draw a syntax-highlighted monospace code block
  (fixed style/position — not configurable yet, per §2 scope).
- If `scene.beat_type == "wait"`: draw the countdown number for that second
  (3, then 2, then 1) over the trademark/background image.

Existing per-scene image compositing, karaoke captions, and music
loop/ducking are unchanged. Ducking is keyed off narration audio presence as
today; the `wait` scene's silence means music is un-ducked and swells during
the countdown — no new logic required for that effect.

## 6. Settings

`backend/app/main.py` `/api/settings` gains two fields (GET/PUT, following
the existing settings pattern) plus two small upload endpoints mirroring the
existing music-upload pattern:

- `POST /api/settings/trademark-image` — stores the file, sets
  `Settings.trademark_background_path`.
- `POST /api/settings/code-quiz-music` — stores the file, sets
  `Settings.default_code_quiz_music_path`.

## 7. Frontend

- Project creation (`Home.jsx`/`StoryStep.jsx`): a content-type choice,
  "Story" (existing) vs "Code Quiz" (new).
- Code Quiz mode shows a dedicated form: Title, Question (multi-line
  textarea), Code snippet (optional, monospace textarea), Answer/Explanation
  (multi-line textarea), optional question/answer image upload, optional
  per-project background image/music override, "Show title card" toggle
  (default on).
- Style step and Images step are skipped entirely in the wizard for Code
  Quiz projects — there is no art style choice and no AI image
  approval screen.
- Voice step is unchanged and reused as-is.
- Music step shows the resolved default (trademark/default track) already
  selected, with the existing picker available to override it.
- Settings page: add trademark background image upload and default
  code-quiz music upload fields.

## 8. Error handling

- Creating a `code_quiz` project with no trademark image set anywhere
  (request override or Settings default) → `400` with a message directing
  the user to set one in Settings.
- Missing music (no override, no default) → not an error; renders without
  music, same as story projects today.
- Overly long question/code/answer text that doesn't fit the overlay box →
  render-time text wrapping/font shrink to fit, not a failure.

## 9. Testing

New `test_code_quiz_flow()` in `backend/tests/smoke_test.py`:

1. Set a dummy trademark image and a dummy short music track via the new
   settings endpoints.
2. Create a project with `content_type="code_quiz"`.
3. `POST` a sample question (with A–D options on separate lines), code
   snippet, and answer/explanation to `/api/projects/{pid}/code-quiz`.
4. Assert exactly 3 scenes come back in order with `beat_type`
   `question`/`wait`/`answer`, verbatim narration text, and `code_text`
   carried onto both `question` and `answer`.
5. Wait for narration synthesis; assert the `wait` scene's
   `audio_duration == 3.0` and the other two match their TTS output.
6. Run render; assert it succeeds and the resulting video's duration is
   approximately `question_audio + 3.0 + answer_audio` (within existing
   transition/pause tolerance, same pattern as the existing duration
   assertions in `run()`).

Existing Hindi/English mythology smoke tests and the self-recording voice
test are untouched.

## 10. Future (not built now)

- Quick-fire: multiple question/wait/answer groups per reel. The beat model
  (ordered `Scene` rows with `beat_type`) already supports this —
  implementation would add a loop over question/answer pairs when creating
  scenes, with no schema change.
- In-app math/diagram rendering (LaTeX → image) instead of manual image
  upload.
- Configurable countdown length/style, configurable code-block styling,
  per-video art style for code quiz.
