# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
tts.py
Text in, a WAV to fetch out.

The Windows build's /api/tts/pause, /resume and /stop are gone. They
existed to drive a pygame mixer living in the backend's worker process;
now that the browser holds the audio element, pausing is
`audio.pause()`. Removing them removed a whole class of state-sync bug,
since there is no longer a server-side playback state for the client's
view of playback to disagree with.

What's left is deliberately small: synthesize, fetch the clip, read and
write settings.
"""

from flask import Blueprint, jsonify, request, send_file

from quietude.core import database, tts_engine

bp = Blueprint("tts", __name__, url_prefix="/api/tts")


def _validate_voice_settings(payload, *, partial=False):
    """Validates rate, volume and voice_id out of a request payload.

    Shared by /settings, which requires all three, and /speak, where the
    same three arrive as optional overrides so the settings page can
    preview what is on screen rather than what is on disk. `partial` is
    the only difference between the two callers, which is deliberate:
    one validator means the preview and the save can never drift into
    disagreeing about what a valid rate is.

    Returns (values, field_errors). `values` holds only the fields that
    were present and valid, so a caller can layer it over the saved
    settings.
    """
    values, errors = {}, {}

    if "rate" in payload or not partial:
        try:
            rate = int(payload.get("rate"))
        except (TypeError, ValueError):
            errors["rate"] = "Rate must be a whole number."
        else:
            if not (tts_engine.RATE_MIN <= rate <= tts_engine.RATE_MAX):
                errors["rate"] = (
                    f"Rate must be between {tts_engine.RATE_MIN} "
                    f"and {tts_engine.RATE_MAX}."
                )
            else:
                values["rate"] = rate

    if "volume" in payload or not partial:
        try:
            volume = float(payload.get("volume"))
        except (TypeError, ValueError):
            errors["volume"] = "Volume must be a number between 0 and 1."
        else:
            if not (0.0 <= volume <= 1.0):
                errors["volume"] = "Volume must be between 0 and 1."
            else:
                values["volume"] = volume

    if "voice_id" in payload or not partial:
        voice_id = (payload.get("voice_id") or "").strip()
        available = {v["id"] for v in tts_engine.get_voices()}
        if not voice_id:
            errors["voice"] = "Please select a voice."
        elif available and voice_id not in available:
            errors["voice"] = (
                "That voice isn't available anymore - please pick another."
            )
        else:
            values["voice_id"] = voice_id

    return values, errors


@bp.get("/status")
def tts_status():
    return jsonify(tts_engine.status())


@bp.post("/speak")
def tts_speak():
    """Synthesizes and returns a clip id. Synchronous - it returns once
    the WAV is on disk, so the browser can play it immediately rather
    than poll to find out whether it exists yet."""
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided."}), 400

    user = database.get_primary_user()
    settings = database.get_tts_settings(user) if user else None

    # The settings page previews the controls as they currently read,
    # not as they were last saved, so it sends the three values with the
    # text. They are validated exactly as a save validates them: an
    # endpoint that takes a number from a request and hands it to a
    # synthesis worker is one that can be made to misbehave, and "it is
    # only the preview button" is not a reason to trust it.
    overrides, field_errors = _validate_voice_settings(payload, partial=True)
    if field_errors:
        return jsonify({
            "ok": False,
            "field_errors": field_errors,
            "error": next(iter(field_errors.values())),
        }), 400

    effective = dict(settings or {})
    effective.update(overrides)

    clip_id = tts_engine.synthesize(
        text,
        rate=effective.get("rate"),
        volume=effective.get("volume"),
        voice_id=effective.get("voice_id"),
    )
    if not clip_id:
        status = tts_engine.status()
        return jsonify({
            "ok": False,
            "error": status.get("error") or "Text-to-speech isn't ready.",
        }), 503

    # The volume the browser should apply to this clip. Sent with the
    # clip rather than fetched separately so a volume change takes effect
    # on the next phrase without a round trip, and without re-synthesis.
    return jsonify({
        "ok": True,
        "clip_id": clip_id,
        "url": f"/api/tts/clip/{clip_id}",
        # The effective volume, so an unsaved volume slider is audible
        # in the preview without a re-synthesis or a round trip.
        "volume": effective.get("volume", 1.0),
    })


@bp.get("/clip/<clip_id>")
def tts_clip(clip_id):
    path = tts_engine.clip_path(clip_id)
    if path is None:
        return jsonify({"ok": False, "error": "No such clip."}), 404
    # Clips are single-use in practice but cheap to re-fetch; a short
    # cache window covers a replay without pinning them in memory.
    return send_file(path, mimetype="audio/wav", max_age=60)


@bp.get("/settings")
def get_tts_settings():
    user = database.get_primary_user()
    return jsonify({
        "ok": True,
        "saved": database.get_tts_settings(user) if user else None,
        "defaults": tts_engine.get_property_defaults(),
        "voices": tts_engine.get_voices(),
        "rate_min": tts_engine.RATE_MIN,
        "rate_max": tts_engine.RATE_MAX,
    })


@bp.post("/settings")
def save_tts_settings():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400

    payload = request.get_json(silent=True) or {}
    values, field_errors = _validate_voice_settings(payload)
    if field_errors:
        return jsonify({"ok": False, "field_errors": field_errors}), 400

    database.set_tts_settings(
        user["id"], values["rate"], values["volume"], values["voice_id"]
    )
    return jsonify({"ok": True})
