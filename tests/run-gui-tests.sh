#!/usr/bin/env bash
#
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The interface, driven in a real browser.
#
# Four faults got into a release because there was nothing checking the
# interface itself: the window could not be made smaller than it opened
# at, the layout pushed the message box off screen whenever anything
# appeared above it, a placeholder still called the assistant by the
# application's name, and spoken words never reached the box. Each one
# now has a test, and they run over every screen at every size.
#
# Nothing here touches your profile: each run gets an empty XDG sandbox
# under tests/gui/run/, the models are stubs, and the microphone is a
# generated WAV file.
#
# Usage:
#   ./tests/run-gui-tests.sh            everything
#   ./tests/run-gui-tests.sh sizes      one suite: screens, sizes, states,
#                                       voice, conversation, allviews,
#                                       commands, scroll
#
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
G="$HERE/gui"
RUN="$G/run"
PORT="${QUIETUDE_TEST_PORT:-5190}"
# The server only answers requests carrying the window's secret. The
# tests are not the window, so they are given the same secret and send
# the same header - which keeps the gate switched on while they run,
# rather than testing a version of the app that does not have it.
export QUIETUDE_TEST_TOKEN="${QUIETUDE_TEST_TOKEN:-suite-$$}"

C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'

need() {
  command -v "$1" >/dev/null 2>&1 || {
    printf '%sMissing %s.%s %s\n' "$C_BAD" "$1" "$C_OFF" "$2"; exit 1; }
}
need python3 ""
python3 -c "import playwright" 2>/dev/null || {
  printf '%sPlaywright is not installed.%s\n' "$C_BAD" "$C_OFF"
  printf '  pip install playwright && playwright install chromium\n'; exit 1; }

[[ -d "$ROOT/frontend/dist" ]] || {
  printf '%sThe interface has not been built.%s\n' "$C_BAD" "$C_OFF"
  printf '  cd frontend && npm install && npm run build\n'; exit 1; }

mkdir -p "$RUN"
[[ -f "$RUN/speech.wav" ]] || python3 "$G/make_audio.py" >/dev/null

start_server() {
  pkill -f "[q]uietude --port $PORT" >/dev/null 2>&1
  # Also anything left from a suite that manages its own servers - a
  # crashed screens or walkthrough run leaves one per size, and they
  # hold their ports until something stops them.
  pkill -f "[q]uietude --port 53" >/dev/null 2>&1
  pkill -f "[q]uietude --port 54" >/dev/null 2>&1
  sleep 1
  rm -rf "$RUN/sandbox"
  mkdir -p "$RUN/sandbox"/{data,config,cache}
  # The markers the model catalogue treats as "a loadable model is
  # here". The stubs never read them; what matters is that voice chat
  # is offered rather than refused.
  mkdir -p "$RUN/sandbox/data/quietude/models/vosk/am" \
           "$RUN/sandbox/data/quietude/models/vosk/conf" \
           "$RUN/sandbox/data/quietude/models/whisper"
  touch "$RUN/sandbox/data/quietude/models/vosk/am/final.mdl" \
        "$RUN/sandbox/data/quietude/models/whisper/model.bin"
  : > "$RUN/spoken.txt"

  env XDG_DATA_HOME="$RUN/sandbox/data" \
      XDG_CONFIG_HOME="$RUN/sandbox/config" \
      XDG_CACHE_HOME="$RUN/sandbox/cache" \
      QUIETUDE_NO_BROWSER=1 \
      QUIETUDE_WINDOW_TOKEN="$QUIETUDE_TEST_TOKEN" \
      QUIETUDE_VOSK_SCRIPT="$RUN/spoken.txt" \
      PYTHONPATH="$G/stubs:$ROOT/backend" \
      python3 -m quietude --port "$PORT" > "$RUN/server.log" 2>&1 &

  for _ in $(seq 1 40); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" && { sleep 2; return 0; }
    # A server left over from an earlier run holds the port, the new one
    # exits immediately, and the suite then spends thirty seconds
    # waiting for a page that was never going to load. Said plainly
    # instead.
    # Both wordings: werkzeug's, and the launcher's own - which it now
    # prints instead, because it checks the port before werkzeug gets
    # the chance to print anything and exit.
    if grep -qE "Address already in use|is already in use" "$RUN/server.log" 2>/dev/null; then
      printf '%sPort %s is already in use.%s\n' "$C_BAD" "$PORT" "$C_OFF"
      printf '  Something is still running from an earlier run:\n'
      pgrep -fa "[q]uietude --port $PORT" | sed 's/^/    /'
      printf "  Stop it, or set QUIETUDE_TEST_PORT to another port.\n"
      return 1
    fi
    sleep 0.5
  done
  printf '%sThe server never came up. Last lines:%s\n' "$C_BAD" "$C_OFF"
  tail -5 "$RUN/server.log"
  return 1
}

stop_server() { pkill -f "[q]uietude --port $PORT" >/dev/null 2>&1 || true; }
trap stop_server EXIT

SUITES=(screens sizes states voice conversation allviews commands scroll)
[[ $# -gt 0 ]] && SUITES=("$@")

# The window itself, which cannot be tested from the page's side: what
# the dock files it under, whether it can be dragged smaller than the
# layout supports, and whether the camera and the microphone actually
# work in the engine that ships - as opposed to in a browser standing in
# for it. Voice chat had been tested only in a plain Chromium, which
# answers for none of the three things that make it work: the media
# permission, the worklet module the audio engine fetches on its own,
# and playing a clip back.
run_window_suites() {
  for script in "$G/electron_window.sh" "$G/electron_camera.sh" \
                "$G/electron_voice.sh"; do
    [[ -f "$script" ]] || continue
    printf '\n%s== %s ==%s\n' "$C_DIM" "$(basename "$script" .sh)" "$C_OFF"
    if bash "$script"; then
      printf '%s  ok%s\n' "$C_OK" "$C_OFF"
    else
      printf '%s  failed%s\n' "$C_BAD" "$C_OFF"
      failed=$((failed + 1))
    fi
  done
}

failed=0
for suite in "${SUITES[@]}"; do
  [[ -f "$G/$suite.py" ]] || { printf '%sNo suite called %s%s\n' "$C_BAD" "$suite" "$C_OFF"; exit 1; }
  printf '\n%s== %s ==%s\n' "$C_DIM" "$suite" "$C_OFF"
  start_server || exit 1
  if QUIETUDE_TEST_PORT="$PORT" python3 "$G/$suite.py" "$RUN/shots/$suite"; then
    printf '%s  ok%s\n' "$C_OK" "$C_OFF"
  else
    printf '%s  failed%s\n' "$C_BAD" "$C_OFF"
    failed=$((failed + 1))
  fi
  stop_server
done

run_window_suites

printf '\n'
if [[ $failed -eq 0 ]]; then
  printf '%sAll suites passed.%s Screenshots: %s\n' "$C_OK" "$C_OFF" "$RUN/shots"
else
  printf '%s%d suite(s) failed.%s Screenshots: %s\n' "$C_BAD" "$failed" "$C_OFF" "$RUN/shots"
fi
exit $failed
