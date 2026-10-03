# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
commands.py
The API behind the user commands page.

Thin on purpose: everything that decides anything is in
core/user_commands.py, which is also what the assistant calls when
somebody says a command out loud. Two callers, one set of rules.
"""

from flask import Blueprint, jsonify, request

from quietude.core import user_commands

# /api/user-commands, not /api/commands: the latter is already the
# built-in command reference, and a blueprint registered second simply
# loses - silently, with the first one answering. The GET came back with
# the wrong list and everything else worked, which is the kind of
# collision that takes a while to see.
bp = Blueprint("user_commands", __name__, url_prefix="/api")


@bp.get("/user-commands")
def list_commands():
    return jsonify({
        "commands": user_commands.all_commands(),
        "running": user_commands.running(),
        "directory": str(user_commands.commands_dir()),
        "default_timeout": user_commands.DEFAULT_TIMEOUT_SECONDS,
        "max_timeout": user_commands.MAX_TIMEOUT_SECONDS,
    })


@bp.post("/user-commands")
def create_command():
    data = request.get_json(silent=True) or {}
    command, errors = user_commands.save(data)
    if errors:
        return jsonify({"field_errors": errors,
                        "error": "That isn't quite right yet."}), 400
    return jsonify({"command": command})


@bp.post("/user-commands/<slug>")
def update_command(slug):
    if user_commands.get(slug) is None:
        return jsonify({"error": "There's no command by that name."}), 404
    data = request.get_json(silent=True) or {}
    command, errors = user_commands.save(data, existing_slug=slug)
    if errors:
        return jsonify({"field_errors": errors,
                        "error": "That isn't quite right yet."}), 400
    return jsonify({"command": command})


@bp.post("/user-commands/delete")
def delete_commands():
    data = request.get_json(silent=True) or {}
    slugs = data.get("slugs") or []
    if not isinstance(slugs, list) or not slugs:
        return jsonify({"error": "Nothing selected."}), 400
    removed, errors = user_commands.delete(slugs)
    return jsonify({
        "removed": removed,
        "error": "; ".join(errors) if errors else None,
    })


@bp.post("/user-commands/<slug>/run")
def run_command(slug):
    """Starts one, from the page's Run button.

    Saying the command out loud goes through the assistant instead, so
    that the reply reads like everything else she says. Both end up in
    user_commands.start, which is where the one-at-a-time lock lives.

    Returns as soon as the command is launched rather than when it
    finishes. A request that waits for a backup to complete is a request
    that times out somewhere in the middle, and the spoken lines have to
    land while the thing is happening anyway - so the result is picked
    up from /activity instead.
    """
    result = user_commands.start(slug)
    return jsonify(result), (409 if result.get("busy") else 200)


@bp.get("/user-commands/activity")
def command_activity():
    """What is running, and what finished since the caller last looked.

    Asked about once a second while something is running, by the page
    and by the console - so it reads nothing from disk. Pass ?since= the
    last result_seq seen and `result` comes back only when it is new,
    with the finished text already assembled.
    """
    try:
        since = int(request.args.get("since") or 0)
    except (TypeError, ValueError):
        since = 0

    state = user_commands.activity()
    fresh = user_commands.take_result(since)
    payload = {"running": state["running"], "result_seq": state["result_seq"]}
    if fresh:
        text, status = user_commands.finished_message(fresh["result"])
        # Through the assistant so that a finishing line written with
        # her name in it reads the same as everything else she says.
        from quietude.api.state import session
        session.refresh_identity()
        payload["finished"] = {
            "name": fresh["result"].get("name"),
            "slug": fresh["result"].get("slug"),
            "text": session.assistant._text(text),
            "status": status,
            "result_seq": fresh["result_seq"],
        }
    return jsonify(payload)


@bp.post("/user-commands/stop")
def stop_running():
    stopped = user_commands.stop()
    if stopped is None:
        return jsonify({"stopped": None, "error": "Nothing is running."}), 200
    return jsonify({"stopped": stopped})
