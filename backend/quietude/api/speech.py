# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
speech.py
The three endpoints the local speech pipeline needs.

    POST /api/speech/audio     raw 16kHz mono int16 PCM, as an
                               octet-stream body - no JSON wrapper, no
                               base64, no format negotiation. The
                               browser's AudioWorklet has already done
                               the resampling, so the bytes go straight
                               onto the worker's queue.
    GET  /api/speech/status    the polled payload, including the three
                               sequence counters the frontend diffs
                               against its own last-seen values.
    POST /api/speech/control   suppress / unsuppress / sleep.

No websocket. Audio is one-way and fire-and-forget, and events are
picked up by polling a counter - which cannot miss an event the way a
boolean flag can, and needs none of the reconnection logic a socket
would. ~200ms polling against a loopback endpoint is not a cost worth
engineering around.
"""

from flask import Blueprint, jsonify, request

from quietude.core import speech_engine

bp = Blueprint("speech", __name__, url_prefix="/api/speech")

# ~250ms of 16kHz mono int16 is 8000 bytes. Anything far beyond that
# isn't a chunk from our worklet, so refuse it rather than queue it.
MAX_CHUNK_BYTES = 64 * 1024


@bp.get("/status")
def speech_status():
    return jsonify(speech_engine.status())


@bp.post("/audio")
def speech_audio():
    chunk = request.get_data(cache=False)
    if not chunk:
        return jsonify({"ok": False, "error": "empty chunk"}), 400
    if len(chunk) > MAX_CHUNK_BYTES:
        return jsonify({"ok": False, "error": "chunk too large"}), 413
    if len(chunk) % 2:
        return jsonify({"ok": False, "error": "not 16-bit aligned"}), 400

    accepted = speech_engine.push_audio(chunk)
    # A dropped chunk is not an error worth surfacing to the UI - it
    # means recognition fell behind for a moment, and the honest response
    # is to say so and carry on, not to retry stale audio.
    return jsonify({"ok": True, "accepted": accepted})


@bp.post("/control")
def speech_control():
    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "").strip()
    if action not in ("suppress", "unsuppress", "sleep"):
        return jsonify({"ok": False, "error": "unknown action"}), 400

    # For "sleep", the caller tells us which wake it was responding to,
    # so a request overtaken by a newer wake can be dropped instead of
    # ending a session that just started. See speech_engine.control.
    wake_seq = payload.get("wake_seq")
    try:
        wake_seq = int(wake_seq) if wake_seq is not None else None
    except (TypeError, ValueError):
        wake_seq = None

    # For "suppress", how long the caller expects to be speaking. The
    # worker treats it as a deadline and lifts suppression itself if
    # nothing comes back to lift it, so a browser that never finishes
    # playing a clip cannot leave her deaf for the rest of the session.
    max_ms = payload.get("max_ms")
    try:
        max_ms = float(max_ms) if max_ms is not None else None
    except (TypeError, ValueError):
        max_ms = None

    speech_engine.control(action, wake_seq=wake_seq, max_ms=max_ms,
                          wake_ok=bool(payload.get("wake_ok")))
    return jsonify({"ok": True})


@bp.post("/sensitivity")
def set_sensitivity():
    """How much louder than the room speech has to be.

    Applied to the running worker rather than stored and waited on: this
    is the control someone reaches for *because* they are not being
    heard, and a setting that needed a restart to test would be close to
    useless."""
    payload = request.get_json(silent=True) or {}
    try:
        level = int(payload.get("level"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Sensitivity must be a whole number."}), 400
    if not (speech_engine.SENSITIVITY_MIN <= level <= speech_engine.SENSITIVITY_MAX):
        return jsonify({
            "ok": False,
            "error": (f"Sensitivity must be between {speech_engine.SENSITIVITY_MIN} "
                      f"and {speech_engine.SENSITIVITY_MAX}."),
        }), 400
    speech_engine.set_sensitivity(level)
    return jsonify({"ok": True, "level": level})
