# Katha Studio

Turn a mythological story into a vertical video for **YouTube Shorts** and **Instagram Reels** —
AI images you approve, a natural Hindi or Indian-English narrator you choose, Ken Burns camera motion,
crossfades, word-by-word captions and background music. Everything runs on your Mac and costs nothing.

## Start it

1. Unzip the folder somewhere (e.g. `~/katha-studio`).
2. Open **Terminal**, go to the folder and run:

   ```bash
   cd ~/katha-studio
   ./start.sh
   ```

   The first run installs FFmpeg, Python and the app's packages with Homebrew (install Homebrew from
   <https://brew.sh> first if you don't have it). Your browser then opens **http://localhost:8000**.
3. Next time, just run `./start.sh` again. Stop the app with **Ctrl+C**.

> macOS may say the script is from an unidentified developer. If so, run `chmod +x start.sh` and try again,
> or right-click → Open once.

## One-time setup (2 minutes, free)

Open **Settings** in the app:

| What | Recommended | How to get it |
|---|---|---|
| Scene writer | **Google Gemini** | Go to <https://aistudio.google.com>, sign in, click **Get API key**, paste it in Settings. Free tier, no card needed. Click **List** to pick the newest Flash model. |
| Images | **Pollinations** | Works without a key but is rate-limited. For steadier results create a free account at <https://enter.pollinations.ai> and paste the `sk_…` key. |
| Images (backup) | Cloudflare Workers AI | Free daily quota. Dashboard → Workers AI → *Use REST API* gives the account ID and a token. |
| Voice | **Microsoft neural** | Nothing to set up. Hindi: Swara, Madhur. Indian English: Neerja, Prabhat. |

Use **Test this** under each section to check it works.

## Making a video

1. **Story** — paste the katha, choose Hindi or English narration, 30/60/90 seconds, and an art style.
2. **Scenes** — the AI splits it into scenes with narration and an image prompt each, plus a *character list*
   (e.g. how Ganesha looks). Edit anything; character descriptions are added to every image so they stay consistent.
3. **Images** — two options per scene are painted automatically. Tap the one you like. *More options*,
   *Edit prompt* or *Upload* your own if none fit.
4. **Voice** — play samples, pick the narrator, adjust speed/pitch, then **Record all scenes**. Listen to each and re-record if needed.
5. **Video** — choose caption style, colours, transitions, music; **Render video**; download the MP4
   (1080×1920, H.264/AAC, −14 LUFS loudness). Copy the suggested title, description and hashtags for the upload.

### Background music
Put royalty-free tracks (mp3/wav/m4a) in the `music/` folder or use **Add a track** on the Video step.
The YouTube Audio Library (in YouTube Studio) is a good free source — check each track's licence.

## Fully offline options (optional)

* **Images on your Mac:** `./start.sh --local-images` installs mflux (FLUX.1-schnell, Apple Silicon). The first image
  downloads the model (~10 GB);
  after that each image takes roughly 30–90 s depending on your Mac. Choose **On this Mac** in Settings.
* **Scene writer on your Mac:** install Ollama from <https://ollama.com>, run `ollama pull qwen2.5:7b`, choose Ollama in Settings.

## Good to know

* Free online services can be busy or change their limits. If one fails, the app tries your other configured
  services (you'll see a note), and the offline options keep you working.
* The Microsoft voices come through the same free service Edge's *Read aloud* uses; it's widely used but unofficial.
* AI images can still vary between scenes. Detailed character descriptions help most; regenerate or upload when a face drifts.
* Rendering a 60-second video takes about 1–2 minutes on an M-series Mac.
* Everything you make lives in `data/` (projects, images, audio, videos). Back it up or delete it as you like.

## Folder layout

```
start.sh                 setup + launch
backend/app/             FastAPI server
  providers/             Gemini/Ollama/Pollinations text, image and voice services
  pipeline/              scene writing, captions (.ass), FFmpeg render
backend/fonts/           Mukta (Devanagari + Latin) for captions, SIL Open Font Licence
backend/tests/           smoke_test.py – full offline run
frontend/                React interface (prebuilt in frontend/dist)
music/                   your background tracks
data/                    your projects (created on first run)
```

Check everything works without internet: `backend/.venv/bin/python backend/tests/smoke_test.py`.
