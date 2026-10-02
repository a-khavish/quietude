# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
setup.py
The first-run account wizard. The state machine itself is unchanged in
core/setup_wizard.py; this is the same two endpoints it always had, so
the Vue setup screen drives it exactly as the old template did.
"""

from flask import Blueprint, jsonify, request

from quietude.api.state import session

bp = Blueprint("setup", __name__, url_prefix="/api/setup")


@bp.get("/state")
def setup_state():
    return jsonify({"ok": True, **session.setup_wizard.state()})


@bp.post("/answer")
def setup_answer():
    payload = request.get_json(silent=True) or {}
    wizard = session.setup_wizard
    result = wizard.submit_text(payload.get("message", ""))

    if result["ok"] and wizard.is_complete():
        register_face = wizard.wants_face_registration()
        user = wizard.finalize()
        session.assistant.set_user(user)
        result["user"] = {"name": user["name"], "age": user["age"]}
        result["register_face"] = register_face

    return jsonify(result)
