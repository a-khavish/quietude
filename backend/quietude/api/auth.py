# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
auth.py
Password login (the backup gate) and the face endpoints.

Both halves are ports. The password flow is the same bounded-attempts
gate, now living in api/state.py so a reset can rebuild it in one place.
The face endpoints are unchanged except for the blueprint wrapper - the
camera is still the browser's, frames still arrive as data URLs, and
OpenCV still does detection and recognition entirely locally.
"""

from flask import Blueprint, jsonify, request

from quietude.core import database, face_auth
from quietude.api.state import session, PasswordLoginFlow

bp = Blueprint("auth", __name__, url_prefix="/api")


# ============================================================
# Password login
# ============================================================

@bp.post("/login/password/start")
def login_password_start():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400
    session.password_login = PasswordLoginFlow()  # a fresh attempt
    return jsonify({"ok": True, **session.password_login.state(user)})


@bp.post("/login/password/answer")
def login_password_answer():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400

    payload = request.get_json(silent=True) or {}
    result = session.password_login.submit(user, payload.get("message", ""))

    if result.get("reset_triggered"):
        session.reset()
        return jsonify(result)

    if result["ok"] and session.password_login.stage == "done":
        session.assistant.set_user(user)
        result["user"] = {"name": user["name"], "age": user["age"]}

    return jsonify(result)


# ============================================================
# Face recognition
# ============================================================

@bp.get("/face/status")
def face_status():
    return jsonify({"registered": face_auth.model_exists()})


@bp.post("/face/reset_samples")
def face_reset_samples():
    """Wipes any captured-but-not-yet-trained samples. Called at the
    start of every registration attempt, and again if one is cancelled,
    so a new attempt never silently resumes an abandoned one - there's no
    guarantee the person at the camera now is the person who started it."""
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400
    face_auth.clear_samples(user["id"])
    return jsonify({"ok": True})


@bp.post("/face/capture")
def face_capture():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400

    payload = request.get_json(silent=True) or {}
    image = payload.get("image")
    if not image:
        return jsonify({"ok": False, "error": "No image received."}), 400

    # Defence in depth: never save past the required count, even if the
    # frontend somehow fires overlapping capture requests.
    existing = face_auth.sample_count(user["id"])
    if existing >= face_auth.MIN_SAMPLES_REQUIRED:
        return jsonify({"ok": True, "detected": True, "count": existing,
                        "required": face_auth.MIN_SAMPLES_REQUIRED})

    try:
        face = face_auth.extract_face(image)
    except face_auth.CascadeLoadError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    if face is None:
        return jsonify({"ok": True, "detected": False,
                        "message": "No face detected - center your face in frame."})

    count = face_auth.save_sample(user["id"], face)
    return jsonify({"ok": True, "detected": True, "count": count,
                    "required": face_auth.MIN_SAMPLES_REQUIRED})


@bp.post("/face/train")
def face_train():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False, "error": "No user registered yet."}), 400

    success = face_auth.train_model(user["id"])
    if success:
        database.set_face_registered(True)
        # The cached user may predate registration (e.g. straight after
        # setup finalized) - refresh so face_registered is true now,
        # rather than only after the next full login.
        session.assistant.set_user(database.get_primary_user())
    return jsonify({"ok": success})


@bp.post("/face/verify")
def face_verify():
    payload = request.get_json(silent=True) or {}
    image = payload.get("image")
    if not image:
        return jsonify({"ok": True, "matched": False, "detected": False})

    try:
        matched, confidence, detected = face_auth.verify(image)
    except face_auth.CascadeLoadError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "matched": matched,
                    "confidence": confidence, "detected": detected})
