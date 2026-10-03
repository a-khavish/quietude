#!/usr/bin/env bash
#
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The Electron window, as the desktop sees it.
#
# Everything checked here is a property of the window rather than of the
# page inside it, so none of it can be tested from the interface's own
# side: what the dock files it under, whether it can be dragged smaller
# than the layout supports, whether it carries an icon at all.
#
# Each of these has already been a released bug once. The dock icon
# alone took three attempts, because the first two fixed real problems
# that were not the problem.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
DISP=":84"
PORT="${QUIETUDE_TEST_PORT:-5290}"
RUN="$HERE/run"
SB="$RUN/electron-sandbox"

pass=0; fail=0
ok()   { printf '  pass  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

cleanup() {
  pkill -f "[q]uietude --port $PORT" >/dev/null 2>&1
  pkill -f "[e]lectron/dist/electron" >/dev/null 2>&1
  pkill -f "[X]vfb $DISP" >/dev/null 2>&1
}
trap cleanup EXIT
cleanup
sleep 1

command -v Xvfb >/dev/null 2>&1 || { echo "  ----  Xvfb is not installed, skipped"; exit 0; }
[[ -x "$ROOT/electron/node_modules/electron/dist/electron" ]] || {
  echo "  ----  Electron is not installed, skipped"; exit 0; }

Xvfb "$DISP" -screen 0 1400x900x24 >/dev/null 2>&1 &
sleep 2

rm -rf "$SB"
mkdir -p "$SB"/{data,config,cache}
mkdir -p "$SB/data/quietude/models/vosk/am" "$SB/data/quietude/models/whisper"
touch "$SB/data/quietude/models/whisper/model.bin"
: > "$RUN/spoken.txt"

# An icon where config.icon_file() looks, so the window has one to carry.
install -Dm644 "$ROOT/branding/png/quietude-256.png" \
  "$SB/data/icons/hicolor/256x256/apps/quietude.png"

DISPLAY="$DISP" \
XDG_DATA_HOME="$SB/data" XDG_CONFIG_HOME="$SB/config" XDG_CACHE_HOME="$SB/cache" \
QUIETUDE_VOSK_SCRIPT="$RUN/spoken.txt" \
PYTHONPATH="$HERE/stubs:$ROOT/backend" \
python3 -m quietude --port "$PORT" > "$RUN/electron.log" 2>&1 &

for _ in $(seq 1 60); do
  grep -q "opened her own window" "$RUN/electron.log" 2>/dev/null && break
  sleep 0.5
done

if grep -q "opened her own window" "$RUN/electron.log"; then
  ok "the window opened"
else
  bad "the window did not open"
  sed -n '1,12p' "$RUN/electron.log" | sed 's/^/        /'
fi

sleep 3
export DISPLAY="$DISP"

# The toplevel is the window with a size constraint on it; Chromium
# makes several X windows and only one of them is the one a dock sees.
WIN=""
for W in $(xwininfo -root -tree | grep -oE '0x[0-9a-f]+' | sort -u); do
  xprop -id "$W" WM_NORMAL_HINTS 2>/dev/null | grep -qi "minimum size" || continue
  WIN="$W"; break
done

if [[ -z "$WIN" ]]; then
  bad "no window with a minimum size - the window never opened"
  tail -12 "$RUN/electron.log" | sed 's/^/        /'
  printf '\n%d passed, %d failed\n' "$pass" "$fail"
  exit 1
fi

CLASS=$(xprop -id "$WIN" WM_CLASS 2>/dev/null)
case "$CLASS" in
  *'"quietude"'*) ok "the dock files it under quietude: ${CLASS#*= }" ;;
  *)              bad "wrong WM_CLASS: $CLASS" ;;
esac

HINT=$(xprop -id "$WIN" WM_NORMAL_HINTS 2>/dev/null | grep -io "minimum size: [0-9]* by [0-9]*")
case "$HINT" in
  *"760 by 520"*) ok "the window manager is given a minimum: $HINT" ;;
  *)              bad "wrong minimum size: ${HINT:-none}" ;;
esac

if xprop -id "$WIN" _NET_WM_ICON 2>/dev/null | grep -q CARDINAL; then
  ok "the window carries its own icon"
else
  bad "no _NET_WM_ICON - a dock would draw an empty square"
fi

GEOM=$(xwininfo -id "$WIN" | grep -E "Width|Height" | awk '{print $2}' | paste -sd x)
case "$GEOM" in
  1180x760) ok "opens at the size it should ($GEOM)" ;;
  *)        bad "opened at $GEOM, expected 1180x760" ;;
esac

# Centred: the gap on the left should match the gap on the right.
X=$(xwininfo -id "$WIN" | awk '/Absolute upper-left X/ {print $4}')
W_=$(xwininfo -id "$WIN" | awk '/Width/ {print $2}')
LEFT=$X; RIGHT=$((1400 - X - W_))
if [[ ${LEFT#-} -gt 0 && $(( LEFT > RIGHT ? LEFT - RIGHT : RIGHT - LEFT )) -le 20 ]]; then
  ok "centred on the screen (${LEFT}px each side)"
else
  bad "not centred: ${LEFT}px left, ${RIGHT}px right"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
