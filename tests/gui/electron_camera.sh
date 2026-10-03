#!/usr/bin/env bash
#
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Runs electron_camera.py against a real Electron window on a virtual
# display. The fake camera is Chromium's own; a container has no webcam
# and the point of the test is the engine's media policy, not the
# hardware.
set -u
ROOT="$(cd "$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")" && pwd)"
RUN="$ROOT/tests/gui/run"
SB="$RUN/ecam"
DISP=":85"; PORT=5291; DBG=9333
pkill -f "[q]uietude --port $PORT" >/dev/null 2>&1
pkill -f "[e]lectron/dist/electron" >/dev/null 2>&1
pkill -f "[X]vfb $DISP" >/dev/null 2>&1
sleep 1
Xvfb "$DISP" -screen 0 1400x900x24 >/dev/null 2>&1 &
sleep 2
rm -rf "$SB"; mkdir -p "$SB"/{data,config,cache}
mkdir -p "$SB/data/quietude/models/vosk/am" "$SB/data/quietude/models/whisper"
touch "$SB/data/quietude/models/whisper/model.bin"
: > "$RUN/spoken.txt"
install -Dm644 "$ROOT/branding/png/quietude-256.png" \
  "$SB/data/icons/hicolor/256x256/apps/quietude.png"
cd "$ROOT"
DISPLAY="$DISP" \
XDG_DATA_HOME="$SB/data" XDG_CONFIG_HOME="$SB/config" XDG_CACHE_HOME="$SB/cache" \
QUIETUDE_VOSK_SCRIPT="$RUN/spoken.txt" \
QUIETUDE_ELECTRON_DEBUG_PORT=$DBG \
 QUIETUDE_ELECTRON_FLAGS="--use-fake-device-for-media-stream --use-fake-ui-for-media-stream" \
PYTHONPATH="$ROOT/tests/gui/stubs:$ROOT/backend" \
python3 -m quietude --port $PORT > "$RUN/ecam.log" 2>&1 &
for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://127.0.0.1:$DBG/json/version" && break
  sleep 0.5
done
sleep 3
DISPLAY="$DISP" python3 "$ROOT/tests/gui/electron_camera.py" $DBG
STATUS=$?
pkill -f "[q]uietude --port $PORT" >/dev/null 2>&1
pkill -f "[e]lectron/dist/electron" >/dev/null 2>&1
pkill -f "[X]vfb $DISP" >/dev/null 2>&1
exit $STATUS
