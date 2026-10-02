# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
system.py
Status, the one-time agreement, the stored profile, and shutdown.

Gone from here, deliberately: /api/heartbeat and the watchdog thread
behind it. The Windows build had the page ping every 3 seconds and shut
the server down when pings stopped, because the UI was a Chrome
app-window and closing it had to mean "Quietude is closed" - otherwise an
orphaned Python process sat in the terminal forever.

That no longer describes how Quietude runs. Started from ./quietude or a systemd
user service, she's a process with her own lifetime, and the browser tab
is a client that connects to her. Tying her life to a tab would mean a
refresh looks like a quit, a second tab is ambiguous, and a laptop
suspending its browser kills the assistant. Shutdown is now only ever
explicit: the SHUT DOWN button, the "shutdown" command, Ctrl+C, or
`systemctl --user stop quietude`. See lifecycle.py.
"""

from flask import Blueprint, jsonify

from quietude import lifecycle
from quietude.core import database, face_auth, gemini_tool, speech_engine, tts_engine
from quietude.api.state import session

bp = Blueprint("system", __name__, url_prefix="/api")

# Just long enough for this response to be written before the server
# stops. The UI calls this only once its power-down chime has actually
# finished playing, so there is nothing left to wait for here - see
# ShutdownView.vue. Guessing a delay long enough to cover the audio was
# how the chime ended up being cut off three seconds early.
SHUTDOWN_GRACE_SECONDS = 0.3


@bp.get("/status")
def status():
    """The single call the SPA makes on load to decide where to route."""
    speech_ready, speech_detail = speech_engine.readiness()
    return jsonify({
        "has_users": database.has_users(),
        "agreement_accepted": database.agreement_accepted(),
        "face_registered": database.face_registered(),
        "setup_in_progress": database.get_setup_progress() is not None,
        "speech": {
            "ready": speech_ready,
            "detail": speech_detail,
            "available": speech_engine.is_available(),
        },
        "tts": tts_engine.status(),
        "gemini_installed": gemini_tool.is_available(),
    })


@bp.post("/agreement/accept")
def accept_agreement():
    database.accept_agreement()
    return jsonify({"ok": True})


@bp.get("/user")
def get_user():
    user = database.get_primary_user()
    if not user:
        return jsonify({"ok": False}), 404
    return jsonify({"ok": True, "user": {
        "name": user["name"],
        "age": user["age"],
        "face_registered": bool(user.get("face_registered")),
    }})


@bp.get("/conversation_mode_status")
def conversation_mode_status():
    assistant = session.assistant
    return jsonify({
        "conversation_mode": quietude.conversation_mode,
        "conversation_turns": {
            "used": len(quietude.gemini_history),
            "cap": gemini_tool.MAX_HISTORY_TURNS,
        },
    })


@bp.get("/login/post_login_check")
def post_login_check():
    """Called once right after a successful login. Returns the
    face-registration nag if the face still isn't registered - repeated
    every login until it is - or a null prompt once it is."""
    session.adopt_existing_user()
    return jsonify({"ok": True, "prompt": session.assistant.post_login_prompt()})


@bp.post("/reset/execute")
def reset_execute():
    """Only reached after the frontend has completed the same live
    face-verification gate used for login."""
    database.factory_reset()
    session.reset()
    face_auth._cascade = None  # the cascade is bundled, not user data, but reload cleanly
    return jsonify({"ok": True})


@bp.post("/shutdown")
def shutdown_now():
    lifecycle.request_shutdown("shutdown requested from the UI", delay=SHUTDOWN_GRACE_SECONDS)
    return jsonify({"ok": True})


@bp.get("/commands")
def commands_reference():
    """The command reference the SPA renders at /commands. Served as data
    from the one source of truth in assistant.py rather than rendered
    into a template, which is what let the separate server-rendered
    commands page go away."""
    from quietude.core.assistant import COMMANDS_REFERENCE
    from quietude.api.state import session

    # The descriptions carry {name} and {wake} where they refer to the
    # assistant rather than to the application, so the reference reads
    # in whatever name the user gave her. Rendered here rather than
    # stored rendered, so renaming her renames the whole reference.
    assistant = session.assistant
    assistant.identity = database.get_assistant()
    rendered = []
    for command in COMMANDS_REFERENCE:
        entry = dict(command)
        for field in ("description", "usage", "name"):
            if isinstance(entry.get(field), str):
                try:
                    entry[field] = assistant._text(entry[field])
                except (KeyError, IndexError):
                    # A stray brace in a description is a typo, not a
                    # reason to fail the whole reference.
                    pass
        rendered.append(entry)
    return jsonify({"ok": True, "commands": rendered})
