#!/usr/bin/env bash
#
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The Python side: the speech state machine, the model catalogue, the
# voice library. No window, no browser, no network - the heavy native
# dependencies are stubbed and everything runs against a throwaway
# profile.
#
#   ./tests/run-backend-tests.sh            everything
#   ./tests/run-backend-tests.sh speech     one suite
#
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'

export PYTHONPATH="$HERE/gui/stubs:$ROOT/backend"

SUITES=(launcher user_commands speech model_catalog voice_library)
[[ $# -gt 0 ]] && SUITES=("$@")

failed=0
for suite in "${SUITES[@]}"; do
  script="$HERE/backend/$suite.py"
  [[ -f "$script" ]] || { printf '%sNo suite called %s%s\n' "$C_BAD" "$suite" "$C_OFF"; exit 1; }
  printf '\n%s== %s ==%s\n' "$C_DIM" "$suite" "$C_OFF"
  if python3 "$script"; then
    printf '%s  ok%s\n' "$C_OK" "$C_OFF"
  else
    printf '%s  failed%s\n' "$C_BAD" "$C_OFF"
    failed=$((failed + 1))
  fi
done

printf '\n'
if [[ $failed -eq 0 ]]; then
  printf '%sAll suites passed.%s\n' "$C_OK" "$C_OFF"
else
  printf '%s%d suite(s) failed.%s\n' "$C_BAD" "$failed" "$C_OFF"
fi
exit $failed
