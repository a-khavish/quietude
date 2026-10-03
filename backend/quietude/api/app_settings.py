# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
app_settings.py
How the application behaves, as opposed to how she behaves.

Two things live here, and they are the two people expect of a desktop
application and were surprised not to find:

    close_action    does closing the window quit, or leave her running
                    in the tray
    start_at_login  does she open when you log in

Both are read by something that is not the interface. The window asks
for close_action when someone closes it, because the window is what has
to act on it. start_at_login is a file in ~/.config/autostart that the
desktop reads at login - see config.set_autostart.

The window's endpoint is deliberately separate from the one the settings
page uses. The page sends a whole settings object; the window wants one
field, as cheaply as possible, at the exact moment the close button is
pressed, and should not be reading anything it does not need.
"""

from flask import Blueprint, jsonify, request

from quietude import config

bp = Blueprint("app_settings", __name__, url_prefix="/api")


@bp.get("/app-settings")
def get_app_settings():
    stored = config.load_settings()
    return jsonify({
        "close_action": stored.get("close_action", config.DEFAULT_CLOSE_ACTION),
        "close_actions": list(config.CLOSE_ACTIONS),
        # Read from the filesystem rather than from the stored setting:
        # the autostart entry can be removed by the desktop's own
        # startup-applications screen, and the truth is the file.
        "start_at_login": config.autostart_enabled(),
    })


@bp.post("/app-settings")
def set_app_settings():
    data = request.get_json(silent=True) or {}
    stored = config.load_settings()
    errors = []

    if "close_action" in data:
        action = str(data["close_action"]).strip().lower()
        if action not in config.CLOSE_ACTIONS:
            errors.append("That isn't a way to close the window.")
        else:
            stored["close_action"] = action

    if "start_at_login" in data:
        wanted = bool(data["start_at_login"])
        ok, error = config.set_autostart(wanted)
        if ok:
            stored["start_at_login"] = wanted
        else:
            errors.append(error)

    config.save_settings(stored)

    return jsonify({
        "close_action": stored.get("close_action", config.DEFAULT_CLOSE_ACTION),
        "close_actions": list(config.CLOSE_ACTIONS),
        "start_at_login": config.autostart_enabled(),
        "error": " ".join(errors) if errors else None,
    }), (400 if errors else 200)


@bp.get("/window/close-action")
def window_close_action():
    """What the window should do when its close button is pressed.

    Asked by the window process itself, at the moment of closing. A
    whole endpoint for one string, because the alternative is the window
    holding a copy that goes stale the moment the setting is changed -
    and a setting that only takes effect after a restart is the kind
    nobody believes they changed.
    """
    stored = config.load_settings()
    return jsonify({
        "close_action": stored.get("close_action", config.DEFAULT_CLOSE_ACTION),
    })
