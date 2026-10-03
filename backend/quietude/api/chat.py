# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
chat.py
The one endpoint that turns a message into a reply, plus file uploads.

A thin wrapper over Quietude.respond() - the command logic is all in
core/assistant.py, exactly as it was. The only thing this layer decides
is that a reply carrying shutdown=True should start the shutdown clock.
"""

import uuid

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from quietude import config, lifecycle
from quietude.api.state import session

bp = Blueprint("chat", __name__, url_prefix="/api")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB per file - generous, not unbounded

# A backstop, not a schedule. Saying "shutdown" in chat should stop Quietude
# even if the UI never confirms - a closed tab mid-chime, say - but in
# the normal case the UI reports its power-down chime finished well
# before this and brings the shutdown forward (see lifecycle.
# request_shutdown). Comfortably longer than the chime, so the audio is
# never the thing cut short.
CHAT_SHUTDOWN_BACKSTOP_SECONDS = 12.0


@bp.post("/chat")
def chat():
    session.adopt_existing_user()
    # Re-read who she is before she answers, so a name changed a second
    # ago is already in effect in this reply rather than the next launch.
    session.refresh_identity()

    payload = request.get_json(silent=True) or {}
    result = session.assistant.respond(payload.get("message", ""))

    if result.get("shutdown"):
        lifecycle.request_shutdown("shutdown command", delay=CHAT_SHUTDOWN_BACKSTOP_SECONDS)

    return jsonify({
        "ok": True,
        "reply": result["text"],
        "expect": result.get("expect", "text"),
        "restart": result.get("restart", False),
        "shutdown": result.get("shutdown", False),
        "voice": result.get("voice"),
        "action": result.get("action"),
        "table": result.get("table"),
        "status": result.get("status"),
        "conversation_turns": result.get("conversation_turns"),
    })


@bp.post("/upload")
def upload_files():
    """Chat attachments. Saved under the XDG data dir's uploads/ folder,
    and wiped by a factory reset along with everything else."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "No files received."}), 400

    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    saved_names = []
    for f in files:
        safe_name = secure_filename(f.filename) or "file"
        f.save(str(config.UPLOADS_DIR / f"{uuid.uuid4().hex}_{safe_name}"))
        saved_names.append(f.filename)

    if len(saved_names) == 1:
        reply = f'Got it - I\'ve saved "{saved_names[0]}".'
    else:
        reply = f"Got it - I've saved {len(saved_names)} files."

    return jsonify({"ok": True, "reply": reply, "count": len(saved_names)})
