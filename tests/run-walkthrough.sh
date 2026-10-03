#!/usr/bin/env bash
#
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Records a walk through the whole application and writes an mp4.
#
# Same sandbox as the tests: an empty profile, stubbed models, a
# generated microphone. Nothing of yours is touched.
#
#   ./tests/run-walkthrough.sh [output.mp4]
#
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
G="$HERE/gui"
RUN="$G/run"
OUT="${1:-$RUN/quietude-walkthrough.mp4}"

python3 -c "import playwright" 2>/dev/null || {
  echo "Playwright is not installed:"
  echo "  pip install playwright && playwright install chromium"; exit 1; }
[[ -d "$ROOT/frontend/dist" ]] || {
  echo "The interface has not been built:"
  echo "  cd frontend && npm install && npm run build"; exit 1; }

mkdir -p "$RUN/video"
rm -f "$RUN/video"/*.webm
[[ -f "$RUN/speech.wav" ]] || python3 "$G/make_audio.py" >/dev/null
: > "$RUN/spoken.txt"

echo "Recording. This takes a few minutes - it walks the whole app."
mapfile -t WEBM < <(python3 "$G/walkthrough.py" "$RUN/video")
[[ ${#WEBM[@]} -gt 0 && -f "${WEBM[0]}" ]] || { echo "Nothing was recorded."; exit 1; }

if ! command -v ffmpeg >/dev/null 2>&1; then
  cp "${WEBM[0]}" "${OUT%.mp4}.webm"
  echo "ffmpeg is not installed, so this is the raw first take:"
  echo "${OUT%.mp4}.webm"
  exit 0
fi

# The takes are recorded at different sizes - the second one is the
# window at its smallest - so each is padded onto the same 1280x800
# canvas before they are joined, and the frame rate is pinned so it
# plays at the right speed rather than at whatever Chromium wrote.
PARTS=()
i=0
for src in "${WEBM[@]}"; do
  part="$RUN/video/part-$i.mp4"
  ffmpeg -nostdin -loglevel error -y -i "$src" \
    -vf "scale=1280:800:force_original_aspect_ratio=decrease,pad=1280:800:(ow-iw)/2:(oh-ih)/2:color=0x05080d,fps=25" \
    -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p "$part" || exit 1
  PARTS+=("$part")
  i=$((i + 1))
done

LIST="$RUN/video/parts.txt"
: > "$LIST"
for part in "${PARTS[@]}"; do printf "file '%s'\n" "$part" >> "$LIST"; done

ffmpeg -nostdin -loglevel error -y -f concat -safe 0 -i "$LIST" \
  -c copy -movflags +faststart "$OUT" || exit 1
rm -f "${PARTS[@]}" "$LIST"
echo "$OUT"
