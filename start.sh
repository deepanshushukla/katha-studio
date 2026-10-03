#!/usr/bin/env bash
# Katha Studio — one-command setup & launch for macOS (Apple Silicon or Intel).
#
#   ./start.sh                 install what's missing, then open the app
#   ./start.sh --local-images  also install mflux (FLUX image model on your Mac, ~10 GB on first use)
#   ./start.sh --voice-change  also install the voice changer (OpenVoice, needs Rust; ~1 GB on first use)
#   ./start.sh --port 8080     use another port
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PORT=8000
LOCAL_IMAGES=0
VOICE_CHANGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local-images) LOCAL_IMAGES=1 ;;
    --voice-change) VOICE_CHANGE=1 ;;
    --port) PORT="$2"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

say() { printf "\033[1;33m▸ %s\033[0m\n" "$*"; }
die() { printf "\033[1;31m✗ %s\033[0m\n" "$*"; exit 1; }

# ---------- Homebrew ----------
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [[ -x $b ]] && eval "$($b shellenv)"; done
fi
command -v brew >/dev/null 2>&1 || die "Homebrew is needed. Install it from https://brew.sh (one command), then run ./start.sh again."

# ---------- FFmpeg with subtitle (libass) support ----------
ffmpeg_ok() {
  local bin="$1"
  [[ -x "$bin" ]] || return 1
  local f; f="$("$bin" -hide_banner -filters 2>/dev/null)" || return 1
  for need in " ass " " zoompan " " xfade " " sidechaincompress " " loudnorm "; do
    grep -q -- "$need" <<<"$f" || return 1
  done
}
BREW_PREFIX="$(brew --prefix)"
FF=""
for cand in "$BREW_PREFIX/opt/ffmpeg-full/bin/ffmpeg" "$(command -v ffmpeg || true)"; do
  if [[ -n "$cand" ]] && ffmpeg_ok "$cand"; then FF="$cand"; break; fi
done
if [[ -z "$FF" ]] && ! command -v ffmpeg >/dev/null 2>&1; then
  say "Installing FFmpeg…"
  brew install ffmpeg
  ffmpeg_ok "$(command -v ffmpeg)" && FF="$(command -v ffmpeg)"
fi
if [[ -z "$FF" ]]; then
  say "Installing the full FFmpeg build (adds subtitle rendering)…"
  brew install ffmpeg-full || die "Could not install ffmpeg-full. Try: brew update && brew install ffmpeg-full"
  FF="$BREW_PREFIX/opt/ffmpeg-full/bin/ffmpeg"
  ffmpeg_ok "$FF" || die "FFmpeg is installed but lacks required filters. Please report: $("$FF" -version | head -1)"
fi
export FFMPEG_BIN="$FF"
export FFPROBE_BIN="$(dirname "$FF")/ffprobe"
say "FFmpeg: $FF"

# ---------- Python 3.10–3.12 ----------
PY=""
for v in 3.12 3.11 3.10; do
  for c in "$BREW_PREFIX/opt/python@$v/bin/python$v" "$(command -v python$v || true)"; do
    [[ -n "$c" && -x "$c" ]] && { PY="$c"; break 2; }
  done
done
if [[ -z "$PY" ]]; then
  say "Installing Python 3.12…"
  brew install python@3.12
  PY="$BREW_PREFIX/opt/python@3.12/bin/python3.12"
fi

VENV="$ROOT/backend/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  say "Creating Python environment…"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi
STAMP="$VENV/.req-$(shasum backend/requirements.txt | cut -c1-12)"
if [[ ! -f "$STAMP" ]]; then
  say "Installing Python packages…"
  "$VENV/bin/pip" install -q -r backend/requirements.txt
  touch "$STAMP"
fi
PW_STAMP="$VENV/.playwright-chromium"
if [[ ! -f "$PW_STAMP" ]]; then
  say "Downloading Chromium for code-quiz rendering (one-time, ~300MB)…"
  "$VENV/bin/playwright" install chromium
  touch "$PW_STAMP"
fi
if [[ $LOCAL_IMAGES == 1 ]]; then
  [[ "$(uname -m)" == "arm64" ]] || die "Local image generation (mflux) needs an Apple Silicon Mac."
  say "Installing mflux (FLUX on Apple Silicon)…"
  "$VENV/bin/pip" install -q -r backend/requirements-local-images.txt
fi
if [[ $VOICE_CHANGE == 1 ]]; then
  command -v cargo >/dev/null 2>&1 || { say "Installing Rust (needed to build the voice changer)…"; brew install rust; }
  say "Installing the voice changer (OpenVoice)…"
  "$VENV/bin/pip" install -q -r backend/requirements-voice-change.txt
fi

# ---------- Frontend (prebuilt in frontend/dist) ----------
if [[ ! -f frontend/dist/index.html ]]; then
  command -v npm >/dev/null 2>&1 || brew install node
  say "Building the web interface…"
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi

# ---------- Run ----------
URL="http://localhost:$PORT"
say "Katha Studio is starting at $URL  (press Ctrl+C to stop)"
( sleep 2; open "$URL" >/dev/null 2>&1 || true ) &
cd backend
exec "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
